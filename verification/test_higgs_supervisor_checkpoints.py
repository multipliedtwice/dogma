import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from contextlib import contextmanager
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from supervise_higgs_preview import checkpoint_activity,refresh_music,copy_music_fields,audio_busy


class SupervisorCheckpointTests(unittest.TestCase):
    def test_reload_can_checkpoint_read_only_asr_but_never_native_generation(self):
        audit=Mock();audit.name.return_value='python.exe';audit.cmdline.return_value=['python','audit_higgs_book_cuda.py']
        owner=Mock();owner.children.return_value=[audit]
        with patch('psutil.Process',return_value=owner):
            self.assertTrue(audio_busy(1));self.assertFalse(audio_busy(1,allow_audit_checkpoint=True))
            native=Mock();native.name.return_value='audiocpp_cli.exe';owner.children.return_value=[native]
            self.assertTrue(audio_busy(1,allow_audit_checkpoint=True))
    def test_successful_music_replaces_old_pending_metadata(self):
        row={'wav_sha256':'speech','music_pending':'old failure','audio_with_music':'old.mp3'}
        copy_music_fields(row,{'music':[],'audio_with_music':'new.mp3'})
        self.assertNotIn('music_pending',row);self.assertEqual(row['wav_sha256'],'speech')
        self.assertEqual(row['audio_with_music'],'new.mp3')
    def test_music_waits_for_speech_publication_and_rejects_stale_audio(self):
        @contextmanager
        def locked(*args,**kwargs):yield False
        @contextmanager
        def unlocked(*args,**kwargs):yield True
        state={'chapters':{'c1':{'wav_sha256':'old'}}}
        with patch('higgs_published_timeline.publication_lock',locked),patch('supervise_higgs_preview.attach_music') as mix:
            self.assertFalse(refresh_music(Path('generation'),'c1',state));mix.assert_not_called()
        with patch('higgs_published_timeline.publication_lock',unlocked),patch('supervise_higgs_preview.read',return_value={'chapters':{'c1':{'wav_sha256':'new'}}}),patch('supervise_higgs_preview.attach_music') as mix:
            self.assertFalse(refresh_music(Path('generation'),'c1',state));mix.assert_not_called()
    def test_replaced_checkpoint_does_not_crash_owner_and_each_stat_is_read_once(self):
        missing=Mock();missing.stat.side_effect=FileNotFoundError('atomic replacement')
        present=Mock();present.stat.return_value=SimpleNamespace(st_mtime_ns=7,st_size=11)
        result=checkpoint_activity([missing,present])
        self.assertEqual(((str(present),7,11),),result)
        missing.stat.assert_called_once();present.stat.assert_called_once()


if __name__=='__main__':unittest.main()
