from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]

from rimsky.util import AppError
from rimsky_training.corpus import create_corpus
from rimsky_training.dataset import build_dataset
from rimsky_training.evaluation import promote_checkpoint, render_evaluation
from rimsky_training.inference import encode_dataset, transcribe
from rimsky_training.pairing import propose_pairs
from rimsky_training.runner import create_plan, execute_training
from rimsky_training.trainer_source import prepare_trainer
from rimsky_training.reporting import dataset_report


def main():
    # Windows consoles may default to a legacy code page that cannot print
    # Russian metadata or even the CLI help text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Offline maintainer pipeline: reviewed book/audio clips → Qwen narrator SFT → evaluated local pack. Never starts from the player.')
    commands = parser.add_subparsers(dest='command', required=True)
    corpus = commands.add_parser('corpus', help='Create an immutable single-narrator corpus from local books and recordings.')
    corpus.add_argument('recipe', type=Path)
    corpus.add_argument('--output', type=Path, required=True)
    recognize = commands.add_parser('transcribe', help='Use local Qwen ASR and forced alignment to propose word timings.')
    recognize.add_argument('--corpus', type=Path, required=True)
    recognize.add_argument('--recording', required=True)
    recognize.add_argument('--asr-model', type=Path, required=True)
    recognize.add_argument('--aligner-model', type=Path, required=True)
    recognize.add_argument('--output', type=Path, required=True)
    recognize.add_argument('--device', choices=['cpu', 'mps', 'cuda:0'], default='cpu')
    recognize.add_argument('--resume', action='store_true')
    pair = commands.add_parser('pair', help='Match independently transcribed words to one explicitly selected book chapter. No automatic acceptance.')
    pair.add_argument('--corpus', type=Path, required=True)
    pair.add_argument('--recording', required=True)
    pair.add_argument('--chapter', required=True)
    pair.add_argument('--transcript', type=Path, required=True)
    pair.add_argument('--output', type=Path, required=True)
    data = commands.add_parser('dataset', help='Cut only explicitly reviewed clips and reserve complete books/chapters for evaluation.')
    data.add_argument('--corpus', type=Path, required=True)
    data.add_argument('--reviews', type=Path, nargs='+', required=True)
    data.add_argument('--reference', required=True)
    data.add_argument('--performance', required=True)
    data.add_argument('--heldout-book', action='append', default=[])
    data.add_argument('--heldout-chapter', action='append', default=[])
    data.add_argument('--output', type=Path, required=True)
    data.add_argument('--ffmpeg')
    encode = commands.add_parser('encode', help='Extract Qwen 12Hz audio codes one clip at a time, with checked resume.')
    encode.add_argument('--dataset', type=Path, required=True)
    encode.add_argument('--tokenizer', type=Path, required=True)
    encode.add_argument('--output', type=Path, required=True)
    encode.add_argument('--device', choices=['cpu', 'mps', 'cuda:0'], default='cuda:0')
    encode.add_argument('--resume', action='store_true')
    report = commands.add_parser('dataset-report', help='Report reviewed decoded hours and coverage by split, without inference or quality claims.')
    report.add_argument('--dataset', type=Path, required=True)
    trainer = commands.add_parser('prepare-trainer', help='Create a separate, narrowly repaired local Qwen trainer; preserve the original checkout.')
    trainer.add_argument('--qwen-source', type=Path, required=True)
    trainer.add_argument('--output', type=Path, required=True)
    plan = commands.add_parser('plan', help='Pin local data, model and upstream trainer. Does not load a model or start training.')
    plan.add_argument('--dataset', type=Path, required=True)
    plan.add_argument('--encoded', type=Path, required=True)
    plan.add_argument('--base-model', type=Path, required=True)
    plan.add_argument('--qwen-source', type=Path, required=True)
    plan.add_argument('--output', type=Path, required=True)
    plan.add_argument('--epochs', type=int, default=3)
    plan.add_argument('--batch-size', type=int, default=1)
    plan.add_argument('--learning-rate', type=float, default=2e-6)
    plan.add_argument('--timeout-seconds', type=int, default=86400)
    train = commands.add_parser('train', help='Explicitly run the inspected upstream single-speaker CUDA/BF16/FlashAttention trainer.')
    train.add_argument('--run', type=Path, required=True)
    train.add_argument('--execute', action='store_true', required=True)
    evaluate = commands.add_parser('evaluate', help='Generate baseline/candidate audio on held-out text; human approval remains required.')
    evaluate.add_argument('--run', type=Path, required=True)
    evaluate.add_argument('--checkpoint', type=Path, required=True)
    evaluate.add_argument('--output', type=Path, required=True)
    evaluate.add_argument('--count', type=int, default=12)
    evaluate.add_argument('--device', choices=['cpu', 'mps', 'cuda:0'], default='cpu')
    publish = commands.add_parser('publish', help='Copy a reviewed checkpoint into a new sealed player pack. Never replaces an installed pack.')
    publish.add_argument('--evaluation', type=Path, required=True)
    publish.add_argument('--review', type=Path, required=True)
    publish.add_argument('--text-model', type=Path, required=True)
    publish.add_argument('--text-license', type=Path, required=True)
    publish.add_argument('--context', type=int, default=16384)
    publish.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'corpus':
            result = create_corpus(args.recipe, args.output)
        elif args.command == 'transcribe':
            result = transcribe(args.corpus, args.recording, args.asr_model, args.aligner_model, args.output, args.device, args.resume)
        elif args.command == 'pair':
            result = propose_pairs(args.corpus, args.recording, args.chapter, args.transcript, args.output)
        elif args.command == 'dataset':
            result = build_dataset(args.corpus, args.reviews, args.output, args.reference, args.performance,
                                   args.heldout_book, args.heldout_chapter, args.ffmpeg)
        elif args.command == 'encode':
            result = encode_dataset(args.dataset, args.tokenizer, args.output, args.device, args.resume)
        elif args.command == 'prepare-trainer':
            result = prepare_trainer(args.qwen_source, args.output)
        elif args.command == 'dataset-report':
            result = dataset_report(args.dataset)
        elif args.command == 'plan':
            result = create_plan(args.dataset, args.encoded, args.base_model, args.qwen_source, args.output,
                                 args.epochs, args.batch_size, args.learning_rate, args.timeout_seconds)
        elif args.command == 'train':
            result = execute_training(args.run)
        elif args.command == 'evaluate':
            result = render_evaluation(args.run, args.checkpoint, args.output, args.count, args.device)
        else:
            result = promote_checkpoint(args.evaluation, args.review, args.text_model, args.text_license, args.output, args.context)
    except (AppError, OSError, ValueError, ImportError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted. Accepted preparation checkpoints are retained; an interrupted optimizer run is not promoted.', file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
