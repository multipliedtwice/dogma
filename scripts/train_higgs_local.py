"""Local QLoRA trainer for audited Higgs voice data, with a real one-step smoke.

Uses the pinned Higgs-v3 training model/processor, NF4 frozen weights and LoRA
only on the decoder projections. Never trains raw source MP3s or human labels
from machine proposals. Run in the isolated higgs-production Python environment.

Counterfactual stress training is opt-in with ``--counterfactual-weight``. A
participating JSONL record supplies ``counterfactual_text`` (configurable), made
from ``text`` by moving exactly one U+0301 mark while retaining identical audio.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import re
import sys
import time
import traceback
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_higgs_training import rows, read, sha, write


ACUTE = '\u0301'
RUSSIAN_VOWELS = frozenset('аеёиоуыэюяАЕЁИОУЫЭЮЯ')
RUSSIAN_WORD = re.compile(r'[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*')
COUNTERFACTUAL_RECIPE = {
    'name': 'Qwen Russian stress counterfactual mark loss',
    'repository': 'siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf',
    'revision': 'c16b3af3db3095120efc1ebb73f6522442bcb3b1',
    'source': ('https://huggingface.co/siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf/'
               'blob/c16b3af3db3095120efc1ebb73f6522442bcb3b1/scripts/train_variant.py'),
    'adaptation': ('Summed codebook NLL margin hinge, with gradients through both text branches. '
                   'Higgs branches are recomputed sequentially to bound CUDA memory.'),
}


def atomic_write(path, value):
    """Atomically replace a JSON report so an interrupted write stays readable."""
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _stress_signature(text):
    """Return canonical plain text and the base-character indexes carrying U+0301."""
    # NFC composes ё and other ordinary combining sequences while leaving the
    # Russian stress acute as its own character (Cyrillic has no precomposed
    # stressed vowel forms in Unicode).
    decomposed = unicodedata.normalize('NFC', text)
    plain, stressed = [], []
    for char in decomposed:
        if char == ACUTE:
            if not plain or plain[-1] not in RUSSIAN_VOWELS:
                raise ValueError('Stress U+0301 must immediately follow a Russian vowel.')
            stressed.append(len(plain) - 1)
        else:
            plain.append(char)
    return unicodedata.normalize('NFC', ''.join(plain)), tuple(stressed)


def _word_at(text, index):
    for match in RUSSIAN_WORD.finditer(text):
        if match.start() <= index < match.end():
            return match.span()
    return None


def validate_counterfactual_text(correct, wrong):
    """Validate a supplied one-word stress move without changing either label.

    The pair must have byte-equivalent letters/punctuation after removing U+0301,
    retain the same number of marks, and move exactly one mark between vowels of
    one Russian word. The function returns NFC text for deterministic hashing.
    """
    if not isinstance(correct, str) or not isinstance(wrong, str) or not correct or not wrong:
        raise ValueError('Counterfactual stress texts must be non-empty strings.')
    correct = unicodedata.normalize('NFC', correct)
    wrong = unicodedata.normalize('NFC', wrong)
    plain_correct, correct_marks = _stress_signature(correct)
    plain_wrong, wrong_marks = _stress_signature(wrong)
    if plain_correct != plain_wrong:
        raise ValueError('Counterfactual text may move U+0301 only; letters and punctuation changed.')
    if not correct_marks or len(correct_marks) != len(wrong_marks):
        raise ValueError('Counterfactual text must preserve at least one stress mark.')
    if len(set(correct_marks)) != len(correct_marks) or len(set(wrong_marks)) != len(wrong_marks):
        raise ValueError('A vowel may carry at most one stress mark.')
    removed = set(correct_marks) - set(wrong_marks)
    added = set(wrong_marks) - set(correct_marks)
    if len(removed) != 1 or len(added) != 1:
        raise ValueError('Counterfactual text must move exactly one stress mark.')
    source, destination = next(iter(removed)), next(iter(added))
    source_word, destination_word = _word_at(plain_correct, source), _word_at(plain_correct, destination)
    if source_word is None or source_word != destination_word:
        raise ValueError('Counterfactual stress must move within the same Russian word.')
    if plain_correct[source] not in RUSSIAN_VOWELS or plain_correct[destination] not in RUSSIAN_VOWELS:
        raise ValueError('Counterfactual stress may move only between Russian vowels.')
    return correct, wrong


def counterfactual_inventory(records, field):
    """Validate and bind all supplied stress pairs by their record index."""
    pairs, marked_without_pair = {}, 0
    digest = hashlib.sha256()
    for index, record in enumerate(records):
        wrong = record.get(field)
        if wrong is None:
            if ACUTE in unicodedata.normalize('NFD', str(record.get('text', ''))):
                marked_without_pair += 1
            continue
        try:
            correct, wrong = validate_counterfactual_text(record.get('text'), wrong)
        except ValueError as error:
            raise ValueError(f'Invalid {field} for record {record.get("id", index)!r}: {error}') from error
        pairs[index] = wrong
        digest.update(json.dumps([record.get('id', index), correct, wrong], ensure_ascii=False,
                                 separators=(',', ':')).encode('utf-8'))
        digest.update(b'\n')
    return pairs, {
        'provided_pairs': len(pairs),
        'marked_without_pair': marked_without_pair,
        'pairs_sha256': digest.hexdigest(),
    }


def verify_dataset(folder):
    manifest = read(folder / 'manifest.json')
    for name, expected in manifest['files'].items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or sha(path) != expected:
            raise ValueError(f'Dataset checksum mismatch: {name}')
    return sha(folder / 'manifest.json')


def prepare_codes(folder, codec_path, output, limit=None, max_seconds=12, longest_first=False):
    """Derived codec features only; source WAVs remain byte-for-byte unchanged."""
    import numpy as np
    import soundfile as sf
    import torch
    from transformers import AutoModel
    verify_dataset(folder)
    candidates = sorted([r for r in rows(folder / 'train.jsonl') if r['metadata']['seconds'] <= max_seconds],
                        key=lambda r: (r['metadata']['seconds'], r['id']), reverse=longest_first)
    if limit:
        candidates = candidates[:limit]
    codec = AutoModel.from_pretrained(codec_path, dtype=torch.float32, local_files_only=True, trust_remote_code=False).eval()
    cache = {}
    def encode(name):
        if name not in cache:
            path = folder / name
            audio, rate = sf.read(path, dtype='float32', always_2d=True)
            if rate != 24000 or audio.shape[1] != 1 or not np.isfinite(audio).all():
                raise ValueError('Audited WAV is not mono 24 kHz.')
            wav = torch.from_numpy(audio.T.copy()).unsqueeze(0)
            with torch.no_grad():
                codes = codec.encode(wav).audio_codes
            cache[name] = codes.squeeze(0).transpose(0, 1).long().tolist()
        return cache[name]
    result = []
    for index, row in enumerate(candidates):
        result.append({**row, 'audio_codes': encode(row['audio']), 'ref_audio_codes': encode(row['ref_audio'])})
        print(f'Codec {index+1}/{len(candidates)} {row["id"]}', flush=True)
        # Save incremental progress to a separate resumable feature file.
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in result), encoding='utf-8')
    del codec
    gc.collect()
    return result


def _rng_state(torch, device_type):
    state = {'cpu': torch.random.get_rng_state().clone()}
    if device_type == 'cuda':
        state['cuda'] = [item.clone() for item in torch.cuda.get_rng_state_all()]
    return state


def _set_rng_state(torch, state):
    torch.random.set_rng_state(state['cpu'])
    if 'cuda' in state:
        torch.cuda.set_rng_state_all(state['cuda'])


def _autocast(torch, device_type, dtype):
    return torch.autocast(device_type, dtype=dtype) if dtype is not None else nullcontext()


def _stress_nll(output, codebook):
    sums = getattr(output, 'all_sum_losses', None)
    tokens = getattr(output, 'all_token_nums', None)
    if sums is None or tokens is None or sums.ndim != 2 or tokens.shape != sums.shape:
        raise RuntimeError('Pinned Higgs model did not return per-sample summed NLL/token counts.')
    if not 0 <= codebook < sums.shape[1]:
        raise ValueError(f'Counterfactual codebook {codebook} is outside 0..{sums.shape[1] - 1}.')
    if (tokens[:, codebook] <= 0).any():
        raise RuntimeError('Counterfactual branch has no target audio tokens.')
    return sums[:, codebook], tokens[:, codebook]


def backward_counterfactual_pair(model, correct_batch, wrong_batch, *, weight, margin,
                                 codebook, gradient_scale=1.0, device_type='cuda', dtype=None):
    """Backpropagate CE + weight*relu(margin - (wrong NLL - correct NLL)).

    Four sequential forwards keep only one autograd graph resident. RNG states are
    replayed so dropout masks in each score probe exactly match its gradient pass.
    Consequently both sides retain the same hinge gradients as a joint graph while
    remaining viable on the 6 GB production-development GPU.
    """
    import torch
    if weight <= 0 or margin <= 0 or gradient_scale <= 0:
        raise ValueError('Counterfactual weight, margin and gradient scale must be positive.')

    correct_before = _rng_state(torch, device_type)
    with torch.no_grad(), _autocast(torch, device_type, dtype):
        correct_probe = model(**correct_batch, use_cache=False)
        correct_nll_probe, correct_tokens = _stress_nll(correct_probe, codebook)
        correct_values = correct_nll_probe.detach().float()
        correct_token_values = correct_tokens.detach().cpu()
    del correct_probe, correct_nll_probe, correct_tokens
    wrong_before = _rng_state(torch, device_type)
    with torch.no_grad(), _autocast(torch, device_type, dtype):
        wrong_probe = model(**wrong_batch, use_cache=False)
        wrong_nll_probe, wrong_tokens = _stress_nll(wrong_probe, codebook)
        wrong_values = wrong_nll_probe.detach().float()
        wrong_token_values = wrong_tokens.detach().cpu()
    wrong_after = _rng_state(torch, device_type)
    del wrong_probe, wrong_nll_probe, wrong_tokens

    if correct_values.shape != wrong_values.shape or not torch.equal(correct_token_values, wrong_token_values):
        raise RuntimeError('Correct and counterfactual branches do not score identical target audio.')
    if not torch.isfinite(correct_values).all() or not torch.isfinite(wrong_values).all():
        raise RuntimeError('Counterfactual score probe produced non-finite NLL.')
    gap = wrong_values - correct_values
    active = gap < margin
    active_float = active.to(device=correct_values.device, dtype=correct_values.dtype)
    hinge = torch.relu(torch.as_tensor(margin, device=gap.device) - gap)

    try:
        _set_rng_state(torch, correct_before)
        with _autocast(torch, device_type, dtype):
            correct_output = model(**correct_batch, use_cache=False)
            if correct_output.loss is None:
                raise RuntimeError('Pinned Higgs model did not return the base CE loss.')
            correct_nll, replay_tokens = _stress_nll(correct_output, codebook)
            if not torch.equal(replay_tokens.detach().cpu(), correct_token_values):
                raise RuntimeError('Correct branch target tokens changed during recomputation.')
            if not torch.isfinite(correct_output.loss) or not torch.isfinite(correct_nll).all():
                raise RuntimeError('Correct stress branch produced a non-finite loss.')
            correct_objective = correct_output.loss + weight * (active_float * correct_nll.float()).mean()
        (correct_objective * gradient_scale).backward()
        base_ce = float(correct_output.loss.detach())
        del correct_output, correct_nll, replay_tokens, correct_objective

        if active.any():
            _set_rng_state(torch, wrong_before)
            with _autocast(torch, device_type, dtype):
                wrong_output = model(**wrong_batch, use_cache=False)
                wrong_nll, replay_tokens = _stress_nll(wrong_output, codebook)
                if not torch.equal(replay_tokens.detach().cpu(), wrong_token_values):
                    raise RuntimeError('Counterfactual branch target tokens changed during recomputation.')
                if not torch.isfinite(wrong_nll).all():
                    raise RuntimeError('Counterfactual stress branch produced a non-finite loss.')
                wrong_objective = -weight * (active_float * wrong_nll.float()).mean()
            (wrong_objective * gradient_scale).backward()
            del wrong_output, wrong_nll, replay_tokens, wrong_objective
    finally:
        # Replaying forwards must consume randomness only once from the caller's view.
        _set_rng_state(torch, wrong_after)

    return {
        'base_ce': base_ce,
        'hinge': float(hinge.mean()),
        'gap_nats': float(gap.mean()),
        'active_pairs': int(active.sum()),
        'pairs': int(active.numel()),
        'objective': base_ce + weight * float(hinge.mean()),
    }


def train(args, report):
    import torch
    from transformers import AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    if not torch.cuda.is_available():
        raise RuntimeError('Local QLoRA requires this machine\'s CUDA GPU.')
    report['gpu'] = {'name': torch.cuda.get_device_name(0), 'total_bytes': torch.cuda.get_device_properties(0).total_memory}
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    torch.cuda.reset_peak_memory_stats()
    dataset_hash = verify_dataset(args.dataset)
    code_path = args.output / 'codes.jsonl'
    if code_path.exists():
        records = rows(code_path)
        # Binding is required for resumes; never silently trust old features.
        binding = read(args.output / 'codes-binding.json')
        if binding['dataset_sha256'] != dataset_hash or binding['codes_sha256'] != sha(code_path):
            raise ValueError('Codec features changed or belong to a different dataset.')
    else:
        records = prepare_codes(args.dataset, args.codec, code_path, 1 if args.smoke else args.limit,
                                args.max_seconds, longest_first=args.smoke)
        write(args.output / 'codes-binding.json', {'dataset_sha256': dataset_hash, 'codes_sha256': sha(code_path),
                                                 'codec_source': read(args.codec.parent / 'codec-source.json')})
    sys.path.insert(0, str(args.upstream))
    from src.dataset import HiggsAudioSFTDataset
    from src.processor import HiggsAudioProcessor
    from model.configuration import HiggsMultimodalQwen3Config
    from model.modeling import HiggsMultimodalQwen3ForConditionalGeneration
    # Production merged checkpoints have the native config and shards but do
    # not carry Hub remote-code helpers.  The pinned upstream class supports
    # both those checkpoints and the original base model.
    config = HiggsMultimodalQwen3Config.from_pretrained(args.model, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True, trust_remote_code=True)
    enc = config.audio_encoder_config
    processor = HiggsAudioProcessor(tokenizer, int(enc['num_codebooks']), int(enc['vocab_size']))
    records = [r for r in records if r['metadata']['seconds'] <= args.max_seconds]
    if not records:
        raise ValueError('No complete clips fit the training duration limit. No sentence was truncated.')
    counterfactual_pairs, counterfactual_summary = ({}, {'provided_pairs': 0, 'marked_without_pair': 0,
                                                          'pairs_sha256': None})
    if args.counterfactual_weight:
        if args.counterfactual_codebook >= int(enc['num_codebooks']):
            raise ValueError(f'Counterfactual codebook must be below {int(enc["num_codebooks"])}.')
        counterfactual_pairs, counterfactual_summary = counterfactual_inventory(
            records, args.counterfactual_field)
        if not counterfactual_pairs:
            raise ValueError(f'Counterfactual training enabled, but no records provide {args.counterfactual_field!r}.')
    dataset = HiggsAudioSFTDataset(records, processor, int(enc['num_codebooks']))
    print('Loading frozen NF4 Higgs weights on CUDA...', flush=True)
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
                              bnb_4bit_compute_dtype=torch.bfloat16, llm_int8_skip_modules=['audio_head'])
    model, loading = HiggsMultimodalQwen3ForConditionalGeneration.from_pretrained(
        args.model, dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True,
        quantization_config=quant, device_map={'': 0}, output_loading_info=True)
    if set(loading.get('missing_keys', [])) - {'audio_head.weight'} or loading.get('mismatched_keys') or loading.get('unexpected_keys'):
        raise RuntimeError('Higgs weights did not load completely: ' + str(loading))
    model.tie_weights()
    if model.audio_head.weight.data_ptr() != model.audio_embedding.weight.data_ptr():
        raise RuntimeError('Higgs audio head must share the pretrained audio embedding.')
    report['audio_head_tied_to_pretrained_embedding'] = True
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                                           gradient_checkpointing_kwargs={'use_reentrant': False})
    # PEFT promotes all frozen nonquantized parameters to fp32. The large text
    # embedding does not need that; keep embeddings/head frozen in bf16.
    model.model.embed_tokens.to(dtype=torch.bfloat16)
    model.audio_embedding.to(dtype=torch.bfloat16)
    model.audio_head.to(dtype=torch.bfloat16)
    lora = LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=.05,
                      target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'], bias='none')
    model = get_peft_model(model, lora)
    model.config.use_cache = False
    model.train()
    trainable = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=.01)
    report.update(dataset_sha256=dataset_hash, codec_features_sha256=sha(code_path),
                  trainable_parameters=sum(p.numel() for p in trainable), training_clips=len(records),
                  quantization='nf4 double-quant', compute_dtype='bf16', rank=args.rank,
                  gradient_accumulation=args.accumulation, base_source=read(args.model.parent / 'base-source.json'))
    report['counterfactual_stress'] = {
        'enabled': bool(args.counterfactual_weight),
        'field': args.counterfactual_field,
        'weight': args.counterfactual_weight,
        'margin_nats': args.counterfactual_margin,
        'codebook': args.counterfactual_codebook,
        'nll_reduction': 'sum over target frames for the selected codebook',
        'gradient_strategy': 'sequential-recompute-v1 (both branches retain gradient)',
        'recipe_provenance': COUNTERFACTUAL_RECIPE,
        **counterfactual_summary,
    }
    atomic_write(args.output / 'training-report.json', report)
    steps = 1 if args.smoke else args.steps
    report['losses'] = []
    report['loss_components'] = []
    report['counterfactual_stress']['pairs_seen'] = 0
    report['counterfactual_stress']['active_pairs_seen'] = 0
    optimizer.zero_grad(set_to_none=True)
    order = list(range(len(dataset)))
    random.shuffle(order)
    start = time.monotonic()
    for step in range(steps):
        loss_sum = base_sum = hinge_sum = gap_sum = 0.
        pairs_seen = active_pairs = 0
        for micro in range(1 if args.smoke else args.accumulation):
            index = order[(step * args.accumulation + micro) % len(order)]
            batch = {k: v.to('cuda') for k, v in dataset.collate_fn([dataset[index]]).items()}
            scale = 1 / (1 if args.smoke else args.accumulation)
            wrong_text = counterfactual_pairs.get(index)
            if wrong_text is not None:
                wrong_sample = dataset._pack_record({**records[index], 'text': wrong_text})
                wrong_batch = {k: v.to('cuda') for k, v in dataset.collate_fn([wrong_sample]).items()}
                metrics = backward_counterfactual_pair(
                    model, batch, wrong_batch, weight=args.counterfactual_weight,
                    margin=args.counterfactual_margin, codebook=args.counterfactual_codebook,
                    gradient_scale=scale, device_type='cuda', dtype=torch.bfloat16)
                del wrong_sample, wrong_batch
            else:
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    output = model(**batch, use_cache=False)
                    loss = output.loss
                if not torch.isfinite(loss):
                    raise RuntimeError('Non-finite Higgs training loss.')
                base_ce = float(loss.detach())
                (loss * scale).backward()
                metrics = {'base_ce': base_ce, 'hinge': 0., 'gap_nats': 0.,
                           'active_pairs': 0, 'pairs': 0, 'objective': base_ce}
                del output, loss
            if not all(torch.isfinite(torch.tensor(metrics[key])) for key in ('base_ce', 'hinge', 'gap_nats', 'objective')):
                raise RuntimeError('Non-finite Higgs counterfactual training metric.')
            loss_sum += metrics['objective']
            base_sum += metrics['base_ce']
            hinge_sum += metrics['hinge'] * metrics['pairs']
            gap_sum += metrics['gap_nats'] * metrics['pairs']
            pairs_seen += metrics['pairs']
            active_pairs += metrics['active_pairs']
            del batch
        torch.nn.utils.clip_grad_norm_(trainable, 1.)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        report['optimizer_steps'] = step + 1
        micros = 1 if args.smoke else args.accumulation
        report['losses'].append(loss_sum / micros)
        report['loss_components'].append({
            'step': step + 1,
            'base_ce': base_sum / micros,
            'counterfactual_hinge': hinge_sum / pairs_seen if pairs_seen else 0.,
            'counterfactual_gap_nats': gap_sum / pairs_seen if pairs_seen else None,
            'counterfactual_pairs': pairs_seen,
            'counterfactual_active_pairs': active_pairs,
            'objective': loss_sum / micros,
        })
        report['counterfactual_stress']['pairs_seen'] += pairs_seen
        report['counterfactual_stress']['active_pairs_seen'] += active_pairs
        report['peak_allocated_bytes'] = torch.cuda.max_memory_allocated()
        report['peak_reserved_bytes'] = torch.cuda.max_memory_reserved()
        report['elapsed_seconds'] = time.monotonic() - start
        atomic_write(args.output / 'training-report.json', report)
        detail = (f', cf={report["loss_components"][-1]["counterfactual_hinge"]:.3f}, '
                  f'gap={report["loss_components"][-1]["counterfactual_gap_nats"]:.2f}'
                  if pairs_seen else '')
        print(f'Step {step+1}/{steps}: loss={report["losses"][-1]:.4f}{detail}, '
              f'peak={report["peak_reserved_bytes"]/2**30:.2f} GiB', flush=True)
    adapter = args.output / 'adapter'
    model.save_pretrained(adapter, safe_serialization=True)
    tokenizer.save_pretrained(adapter)
    report['checkpoint_sha256'] = sha(adapter / 'adapter_model.safetensors')
    report['status'] = 'smoke_passed' if args.smoke else 'trained_pending_validation'
    atomic_write(args.output / 'training-report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / 'training-work/higgs-production'
    parser.add_argument('--dataset', type=Path, default=base / 'datasets/reviewed-20261012-v1')
    parser.add_argument('--model', type=Path, default=base / 'models/base')
    parser.add_argument('--codec', type=Path, default=base / 'models/codec')
    parser.add_argument('--upstream', type=Path, default=base / 'upstream/finetune')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--steps', type=int, default=100)
    parser.add_argument('--accumulation', type=int, default=8)
    parser.add_argument('--rank', type=int, default=8)
    parser.add_argument('--max-seconds', type=float, default=12)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--learning-rate', type=float, default=2e-5)
    parser.add_argument('--seed', type=int, default=20261012)
    parser.add_argument('--counterfactual-weight', type=float, default=0.,
                        help='Enable supplied wrong-stress margin loss at this weight (recommended pilot: 0.1).')
    parser.add_argument('--counterfactual-margin', type=float, default=5.,
                        help='Required wrong-minus-correct summed NLL margin in nats.')
    parser.add_argument('--counterfactual-codebook', type=int, default=0,
                        help='Audio codebook whose summed target NLL defines the stress margin.')
    parser.add_argument('--counterfactual-field', default='counterfactual_text',
                        help='JSONL field containing the provided wrong-mark text alternative.')
    args = parser.parse_args()
    if min(args.steps, args.accumulation, args.rank, args.max_seconds) <= 0:
        parser.error('Training sizes must be positive.')
    if args.counterfactual_weight < 0 or args.counterfactual_margin <= 0 or args.counterfactual_codebook < 0:
        parser.error('Counterfactual weight/codebook must be non-negative and margin must be positive.')
    if not args.counterfactual_field.strip():
        parser.error('Counterfactual field must not be empty.')
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['WANDB_MODE'] = 'disabled'
    report = {'schema': 2, 'status': 'running', 'builder_sha256': sha(Path(__file__)),
              'upstream_commit': 'c79f245bf0dec986c89db64581e981e2825f69e9',
              'versions': {n: importlib.metadata.version(n) for n in ('torch', 'transformers', 'peft', 'bitsandbytes')},
              'requested_training': {
                  'steps': 1 if args.smoke else args.steps,
                  'accumulation': 1 if args.smoke else args.accumulation,
                  'rank': args.rank,
                  'learning_rate': args.learning_rate,
                  'seed': args.seed,
                  'max_seconds': args.max_seconds,
                  'limit': 1 if args.smoke else args.limit,
                  'counterfactual_weight': args.counterfactual_weight,
                  'counterfactual_margin_nats': args.counterfactual_margin,
                  'counterfactual_codebook': args.counterfactual_codebook,
                  'counterfactual_field': args.counterfactual_field,
              }}
    atomic_write(args.output / 'training-report.json', report)
    try:
        result = train(args, report)
        print(json.dumps(result, indent=2))
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
        atomic_write(args.output / 'training-report.json', report)
        raise


if __name__ == '__main__':
    main()
