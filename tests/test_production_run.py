import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
# Repo tests layout, with isolated handoff fallback.
CANDIDATE = Path(__file__).resolve().parents[1]/"tools"/"production"
sys.path.insert(0, str(CANDIDATE if CANDIDATE.is_dir() else Path(__file__).resolve().parent))
import production_run as p

class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.rid = 'demo-1'
        p.init(self.root, self.rid, game='test-game', player='test-player')
        self.source = self.root/'video.bin'; self.source.write_bytes(b'test media bytes only; NOT a real recording')
        self.raw = self.root/'game.json'; self.raw.write_text('{"state":"done"}')
    def tearDown(self): self.tmp.cleanup()
    def report(self, name, obj):
        target = self.root/name
        target.write_text(json.dumps(obj), encoding='utf-8'); return target
    def capture(self, **kw):
        o = dict(producer='agent-capture',run_id=self.rid,capture_id='capture-1',status='recording',
                 first_video_frame=True,audio_scope='app',observed_at=time.time())
        o.update(kw); return self.report('capture.json', o)
    def op(self, action, report=None, **kw):
        return p.change(self.root,self.rid,p.read(self.root,self.rid)['revision'],action,report,**kw)
    def play(self):
        self.op('record-ready',self.capture()); self.op('play-start',self.capture())
    def finish(self, good=True):
        self.play()
        self.op('game-finish', self.report('result.json', dict(producer='game-adapter',run_id=self.rid,
                outcome='lost', evidence=[p.artifact(self.raw)])),outcome='lost')
        return self.op('capture-finish',self.capture(status='stopped',media=[p.artifact(self.source)],
                checks={'container_readable':True,'video_continuity':good},unexpected_stop=False))
    def delivery(self, status='ready', **kw):
        final = self.root/'final.bin'; final.write_bytes(b'final output fixture only')
        review = self.root/'review.json'; review.write_text('{"fixture":true}')
        o = dict(producer='gameplay-postproduction',run_id=self.rid,source_sha256=[p.artifact(self.source)['sha256']],
                 final=p.artifact(final),review_evidence=[p.artifact(review)],status=status,
                 ready_exit_code=0,audio_review='passed',picture_review='passed',unresolved_issues=[])
        o.update(kw); return self.report('post.json',o)
    def test_requires_ready_before_play(self):
        with self.assertRaises(p.RunError): self.op('play-start',self.capture())
    def test_rejects_stale_and_missing_frame(self):
        for changes in ({'observed_at':time.time()-30},{'observed_at':float('nan')},
                        {'first_video_frame':False},{'status':'stopped'},{'audio_scope':'system'},
                        {'producer':'fake'},{'run_id':'different'}):
            with self.subTest(changes=changes):
                with self.assertRaises(p.RunError): self.op('record-ready',self.capture(**changes))
    def test_no_sound_before_play_is_not_deadlock(self):
        self.play(); self.assertEqual(p.read(self.root,self.rid)['stage'],'playing')
    def test_revision_conflict_and_no_second_game(self):
        self.play()
        with self.assertRaises(p.RunError): p.change(self.root,self.rid,0,'issue',note='x')
        with self.assertRaises(p.RunError): self.op('play-start',self.capture())
    def test_wrong_capture_and_result(self):
        self.op('record-ready',self.capture())
        with self.assertRaises(p.RunError): self.op('play-start',self.capture(capture_id='another'))
        self.op('play-start',self.capture())
        with self.assertRaises(p.RunError):
            self.op('game-finish',self.report('badgame.json',{'producer':'game-adapter','run_id':self.rid,
                'outcome':'won','evidence':[p.artifact(self.raw)]}),outcome='lost')
    def test_three_outcomes_independent(self):
        r=self.finish(); self.assertEqual((r['game_result'],r['capture_result']),('lost','complete'))
        self.op('edit-start'); r=self.op('deliver',self.delivery())
        self.assertEqual(r['stage'],'delivered'); self.assertEqual(r['game_result'],'lost')
    def test_partial_recording_never_full_delivery(self):
        self.finish(good=False); self.op('edit-start'); r=self.op('deliver',self.delivery())
        self.assertEqual(r['stage'],'needs_review'); self.assertEqual(r['capture_result'],'partial')
    def test_recorder_stops_while_playing(self):
        self.play()
        r=self.op('capture-finish',self.capture(status='failed',media=[p.artifact(self.source)],
                    checks={'container_readable':True,'video_continuity':False},unexpected_stop=True))
        self.assertEqual(r['game_result'],'interrupted'); self.assertEqual(r['capture_result'],'partial')
    def test_partial_postproduction_deliverable_without_false_pass(self):
        self.finish(); self.op('edit-start')
        r=self.op('deliver',self.delivery(status='needs_review',audio_review='unknown'))
        self.assertEqual(r['stage'],'needs_review')
    def test_unknown_listening_or_bool_rc_never_ready(self):
        self.finish(); self.op('edit-start')
        for kw in ({'audio_review':'unknown'},{'ready_exit_code':False},{'unresolved_issues':['bad word']}):
            with self.subTest(kw=kw):
                with self.assertRaises(p.RunError): self.op('deliver',self.delivery(**kw))
    def test_modified_source_and_other_source_rejected(self):
        self.finish(); self.op('edit-start')
        with self.assertRaises(p.RunError): self.op('deliver',self.delivery(source_sha256=['a'*64]))
        self.source.write_bytes(b'different!')
        with self.assertRaises(p.RunError): p.handoff(self.root,self.rid)
    def test_handoff_only_after_stop(self):
        self.play()
        with self.assertRaises(p.RunError): p.handoff(self.root,self.rid)
    def test_identity_and_no_clobber(self):
        with self.assertRaises(FileExistsError): p.init(self.root,self.rid,game='x',player='y')
        with self.assertRaises(p.RunError): p.init(self.root,'../bad',game='x',player='y')
        with self.assertRaises(p.RunError): p.change(self.root,self.rid,True,'issue',note='a')
        self.assertEqual(p.read(self.root,self.rid)['revision'],0)
    def test_handoff_preserves_game_identity(self):
        self.finish()
        h=p.handoff(self.root,self.rid)
        self.assertEqual((h['game'],h['player'],h['game_result']),('test-game','test-player','lost'))
        self.assertIn('correct scoring',h['not_proof_of'])

    def test_edit_start_resumes_after_needs_review(self):
        """后期被判 needs_review 后，必须能在**同一个 run 上继续**修片。

        否则后期一失败整条 run 就死了，只剩"造假"或"重玩一遍"两条路。
        这里同时钉住：续后期时原片必须没被动过。
        """
        self.finish(); self.op('edit-start')
        r = self.op('deliver', self.delivery(status='needs_review', audio_review='unknown'))
        self.assertEqual(r['stage'], 'needs_review')
        # 关键：从 needs_review 还能再进 editing（同一 run，不重开）
        r2 = self.op('edit-start')
        self.assertEqual(r2['stage'], 'editing')
        self.assertEqual(r2['game_result'], 'lost')          # 对局结论没有被重玩覆盖
        self.assertEqual(p.read(self.root,self.rid)['revision'], r2['revision'])
        # 原片被换掉就不许续（那不是同一次录制了）
        self.source.write_bytes(b'tampered source media')
        with self.assertRaises(p.RunError): self.op('edit-start')

    def test_redelivery_keeps_previous_final(self):
        """同一 run 重试后期时，**旧成片不能被静默覆盖**。

        否则"这次交付的是哪一版"无从追溯。当前版放 final，被替换的进 final_history。
        """
        self.finish(); self.op('edit-start')
        self.op('deliver', self.delivery(status='needs_review', audio_review='unknown'))
        first_final = p.read(self.root,self.rid)['artifacts']['final']['sha256']
        self.op('edit-start')
        # 第二版用**不同的**成片文件与报告路径
        final2 = self.root/'final-v2.bin'; final2.write_bytes(b'second, different final output')
        review2 = self.root/'review-v2.json'; review2.write_text('{"fixture":2}')
        rep2 = self.report('post-v2.json', dict(
            producer='gameplay-postproduction', run_id=self.rid,
            source_sha256=[p.artifact(self.source)['sha256']],
            final=p.artifact(final2), review_evidence=[p.artifact(review2)],
            status='ready', ready_exit_code=0, audio_review='passed',
            picture_review='passed', unresolved_issues=[]))
        r = self.op('deliver', rep2)
        self.assertEqual(r['stage'], 'delivered')
        self.assertEqual(r['artifacts']['final']['sha256'], p.artifact(final2)['sha256'])
        hist = r['artifacts'].get('final_history') or []
        self.assertEqual(len(hist), 1, 'first final must be retained')
        self.assertEqual(hist[0]['final']['sha256'], first_final)
        self.assertEqual(hist[0]['postproduction_result'], 'needs_review')
        # 历史成片仍可核验（文件还在、哈希一致）
        p.verify_artifact(hist[0]['final'])

    def test_issue_does_not_reset_game(self):
        self.play(); r=self.op('issue',note='audio meter currently silent')
        self.assertEqual(r['stage'],'playing'); self.assertEqual(len(r['issues']),1)

if __name__=='__main__': unittest.main(verbosity=2)
