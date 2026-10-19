"""Job routing, publication, diagnostics and responsive desktop state."""
from dataclasses import asdict
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import patcher_core as core
import worker


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.romfs=self.root/'romfs';self.romfs.mkdir()
        self.mod=self.root/'voices.zip';self.mod.write_bytes(b'fixture')
        self.movies=self.root/'movies.zip';self.movies.write_bytes(b'fixture')
        self.base=self.root/'output';self.base.mkdir()
        self.job=self.base/'job';self.job.mkdir()
        self.calls=[]

    def tearDown(self):self.temp.cleanup()

    def spec(self,game='kh2',mode='all',action='build'):
        return core.JobSpec(game=game,mode=mode,action=action,romfs=str(self.romfs),mod=str(self.mod),
                            movies=str(self.movies),output_base=str(self.base))

    def fake_engine(self,game,fail=False,wrong_id=False):
        def build(*args,**kwargs):
            self.calls.append((args,kwargs))
            output=Path(args[3] if game=='kh2' and isinstance(args[2],Path) and args[2]==self.movies else args[2])
            # build_all(root, source, movies, output, id); other builds use output at index 2.
            if len(args)>4:output=Path(args[3])
            output.mkdir(parents=True)
            if fail:raise ValueError('simulated archive failure')
            ident='010090C022978000' if wrong_id else core.GAMES[game]['id']
            folder=output/'atmosphere/contents'/ident/'romfs';folder.mkdir(parents=True)
            (folder/'verified.hed').write_bytes(b'verified fixture')
            report={'voices_completed':True,'movies_completed':True}
            (output/'build-result.json').write_text(json.dumps(report))
            (output/'movie-result.json').write_text(json.dumps(report))
        def diagnose(*args,**kwargs):
            self.calls.append((args,kwargs))
            path=Path(args[1] if game=='kh1' else args[2]);path.parent.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(path,'w') as z:z.writestr('diagnose.json','{}')
            return {}
        return types.SimpleNamespace(build=build,build_all=build,build_movies=build,diagnose=diagnose,
                                     PatchSource=lambda path,**kwargs:contextlib.nullcontext('loaded-source'))

    def run_worker(self,spec,engine):
        request=dict(asdict(spec),job_root=str(self.job))
        with mock.patch.object(worker,'find_tools',return_value={'ffmpeg':'ffmpeg','ffprobe':'ffprobe'}):
            return worker.run_job(request,engine)

    def test_fixed_ids_have_16_hex_digits(self):
        self.assertEqual(core.GAMES['kh1']['id'],'010090C022978001')
        self.assertEqual(core.GAMES['kh2']['id'],'010090C022978003')
        for game in core.GAMES.values():self.assertEqual(len(game['id']),16)

    def test_voice_language_is_validated_and_persisted_in_build_report(self):
        spec=self.spec('kh1','voices')
        spec.voice_language='ja'
        result=self.run_worker(spec,self.fake_engine('kh1'))
        self.assertTrue(result['success'])
        self.assertEqual(self.calls[0][1]['voice_language'],'ja')
        report=json.loads((self.job/'Berichte/build-result.json').read_text())
        self.assertEqual(report['voice_language'],'ja')
        invalid=self.spec('kh1','voices');invalid.voice_language='fr'
        with self.assertRaisesRegex(ValueError,'German or Japanese'):
            invalid.validate()

    def test_kh1_passes_fixed_id_and_publishes_verified_tree(self):
        result=self.run_worker(self.spec('kh1','voices'),self.fake_engine('kh1'))
        self.assertTrue(result['success'])
        self.assertEqual(self.calls[0][0][3],core.GAMES['kh1']['id'])
        self.assertTrue((self.job/'atmosphere/contents/010090C022978001/romfs').is_dir())
        self.assertFalse((self.job/'Berichte/atmosphere').exists())

    def test_kh2_combined_passes_both_sources_tools_and_fixed_id(self):
        result=self.run_worker(self.spec(),self.fake_engine('kh2'))
        args,kwargs=self.calls[0]
        self.assertEqual(args[1],'loaded-source')
        self.assertEqual(args[2],self.movies)
        self.assertEqual(args[4],core.GAMES['kh2']['id'])
        self.assertEqual(kwargs['ffprobe'],'ffprobe')
        self.assertTrue((Path(result['output'])/'contents/010090C022978003/romfs').is_dir())

    def test_kh2_voice_only_and_movie_only_dispatch(self):
        for mode in ('voices','movies'):
            with self.subTest(mode=mode):
                self.job=self.base/mode;self.job.mkdir()
                result=self.run_worker(self.spec(mode=mode),self.fake_engine('kh2'))
                self.assertTrue(result['success'])
                self.assertEqual(self.calls[-1][0][3],core.GAMES['kh2']['id'])

    def test_failure_never_publishes_atmosphere(self):
        with self.assertRaisesRegex(ValueError,'archive failure'):
            self.run_worker(self.spec(),self.fake_engine('kh2',fail=True))
        self.assertFalse((self.job/'atmosphere').exists())

    def test_wrong_backend_id_blocks_publication(self):
        with self.assertRaisesRegex(ValueError,'fixed Program ID'):
            self.run_worker(self.spec('kh1','voices'),self.fake_engine('kh1',wrong_id=True))
        self.assertFalse((self.job/'atmosphere').exists())

    def test_cancel_flag_blocks_final_publication(self):
        (self.job/'Abbrechen.flag').touch()
        with self.assertRaises(InterruptedError):self.run_worker(self.spec(),self.fake_engine('kh2'))
        self.assertFalse((self.job/'atmosphere').exists())

    def test_kh1_diagnosis_does_not_need_mod(self):
        spec=self.spec('kh1','voices','diagnose');spec.mod=''
        result=self.run_worker(spec,self.fake_engine('kh1'))
        self.assertTrue(result['diagnosis'])
        self.assertFalse((self.job/'atmosphere').exists())
        self.assertEqual(self.calls[0][0][2],core.GAMES['kh1']['id'])

    def test_output_inside_inputs_is_rejected(self):
        for path in (self.romfs,self.romfs/'nested'):
            spec=self.spec();spec.output_base=str(path)
            with self.assertRaisesRegex(ValueError,'outside'):spec.validate()

    def test_wrong_game_and_missing_inputs_are_rejected(self):
        spec=self.spec();spec.game='kh3'
        with self.assertRaises(ValueError):spec.validate()
        spec=self.spec();spec.movies=''
        with self.assertRaisesRegex(ValueError,'STEAM'):spec.validate()

    def test_progress_is_monotonic_and_never_complete_before_result(self):
        tracker=core.ProgressTracker(self.spec())
        values=[tracker.consume(line)['value'] for line in ['[1/3] Deutsche Stimmen prüfen',
                 'Native Patchdateien geprüft: 6500/6526','Native Stimmen und Ressourcen bauen: kh2_fifth',
                 'Alle eingebauten Dateien und unveränderten Ressourcen prüfen ...','[2/3] Filme',
                 '[12/12] sec2.mp4: copy ...','[1/12] ena.mp4 prüfen ...','[3/3] Ausgabe']]
        self.assertEqual(values,sorted(values));self.assertLess(max(values),100)

    def test_diagnostic_contains_reports_but_no_full_game_media(self):
        (self.job/'Ergebnis.json').write_text('{}')
        reports=self.job/'Berichte';reports.mkdir()
        (reports/'voice-result.json').write_text('{}')
        (reports/'large.pkg').write_bytes(b'private game data')
        (reports/'film.mp4').write_bytes(b'private movie')
        with zipfile.ZipFile(reports/'KH2-Switch-Diagnose.zip','w') as z:z.writestr('diagnose.json','{}')
        atmosphere=self.job/'atmosphere';atmosphere.mkdir();(atmosphere/'anything.json').write_text('{}')
        with zipfile.ZipFile(core.diagnostic_bundle(self.job)) as z:
            self.assertIn('Berichte/voice-result.json',z.namelist())
            self.assertIn('Berichte/KH2-Switch-Diagnose.zip',z.namelist())
            self.assertFalse(any(n.endswith(('.pkg','.mp4')) or n.startswith('atmosphere/') for n in z.namelist()))

    def test_explicit_tools_folder_does_not_silently_use_path(self):
        folder=self.root/'tools';folder.mkdir()
        with mock.patch.object(core.shutil,'which',return_value='/elsewhere/ffmpeg'):
            with self.assertRaisesRegex(ValueError,'ffmpeg is missing'):core.find_tools(str(folder))
        for name in ('ffmpeg.exe','ffprobe.exe'):(folder/name).write_bytes(b'fixture')
        self.assertEqual(core.find_tools(str(folder))['ffmpeg'],str(folder/'ffmpeg.exe'))

    def test_worker_command_separates_paths_with_spaces(self):
        path=self.root/'a folder/request.json'
        command=core.worker_command(path)
        self.assertEqual(command[-1],str(path));self.assertIn('worker.py',command[-2])

    def test_crashed_worker_never_leaves_published_tree_and_preserves_diagnostic(self):
        application=self.root/'test application';application.mkdir()
        (application/'worker.py').write_text('''
import json,sys
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
p=Path(r['job_root'])/'atmosphere';p.mkdir()
(p/'partial.pkg').write_bytes(b'incomplete')
print('Fehler beim abschließenden Schritt',flush=True)
raise SystemExit(5)
''',encoding='utf-8')
        runner=core.JobRunner()
        with mock.patch.object(core,'RESOURCE_ROOT',application):
            runner.start(self.spec('kh1','voices'))
            runner.thread.join(timeout=15)
        self.assertFalse(runner.running)
        events=[]
        while not runner.events.empty():events.append(runner.events.get_nowait())
        result=next(e for e in events if e['type']=='finished')
        self.assertFalse(result['success'])
        root=Path(result['root'])
        self.assertFalse((root/'atmosphere').exists())
        self.assertTrue((root/'Berichte/Nicht-freigegeben/atmosphere/partial.pkg').is_file())
        with zipfile.ZipFile(result['diagnostic']) as archive:
            self.assertIn('Berichte-GUI/GUI-Protokoll.log',archive.namelist())
            self.assertFalse(any(n.endswith('.pkg') for n in archive.namelist()))


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','GUI display required')
class DesktopTests(unittest.TestCase):
    def setUp(self):
        import kh_gui
        with mock.patch.object(kh_gui,'load_settings',return_value={}):self.app=kh_gui.PatcherApp(auto_setup=False)
        self.app.update()

    def tearDown(self):self.app.destroy()

    def test_game_selection_and_remembered_paths(self):
        self.assertEqual(self.app.game,'kh2');self.assertEqual(self.app.mode,'all')
        self.app.select_voice_language('ja')
        self.app.romfs.value.set('KH2 ROMFS')
        self.app.select_game('kh1');self.app.update()
        self.assertEqual(self.app.mode,'voices');self.assertFalse(self.app.movies.winfo_ismapped())
        self.assertEqual(self.app.voice_language.get(),'de')
        self.app.select_voice_language('ja')
        self.app.romfs.value.set('KH1 ROMFS')
        self.app.select_game('kh2');self.app.update()
        self.assertEqual(self.app.romfs.value.get(),'KH2 ROMFS')
        self.assertEqual(self.app.voice_language.get(),'ja')
        self.assertTrue(self.app.movies.winfo_ismapped())

    def test_japanese_mod_links_follow_game_and_voice_language(self):
        import kh_gui
        with mock.patch.object(kh_gui.webbrowser,'open') as open_link:
            self.app.select_voice_language('ja')
            self.app.open_voice_mod()
            open_link.assert_called_with('https://www.nexusmods.com/kingdomhearts2finalmix/mods/16?tab=files')
            self.app.select_game('kh1')
            self.app.select_voice_language('ja')
            self.app.open_voice_mod()
            open_link.assert_called_with('https://www.nexusmods.com/kingdomheartsfinalmix/mods/6?tab=files')

    def test_busy_state_disables_mutations_and_keeps_log_available(self):
        self.app.set_busy(True)
        self.app.select_game('kh1');self.app.select_mode('movies')
        self.assertEqual(self.app.game,'kh2');self.assertEqual(self.app.mode,'all')
        self.app.show_page('log');self.app.update()
        self.assertTrue(self.app.log.winfo_ismapped())
        self.app.append_log('FFmpeg arbeitet weiter')
        self.assertIn('FFmpeg is still working',self.app.log.get('1.0','end'))

    def test_failure_opens_live_log_and_preserves_error(self):
        self.app.set_busy(True)
        self.app.finished({'success':False,'error':'Specific conversion failure'})
        self.app.update()
        self.assertFalse(self.app.busy);self.assertEqual(self.app.page,'log')
        self.assertIn('Specific conversion failure',self.app.log.get('1.0','end'))

    def test_success_and_diagnosis_have_distinct_completion_text(self):
        self.app.finished({'success':True,'diagnosis':True})
        self.assertIn('Diagnostics complete',self.app.status.cget('text'))
        self.app.finished({'success':True,'diagnosis':False})
        self.assertIn('patch is ready',self.app.status.cget('text'))
        self.assertEqual(self.app.progress_value,100)

    def test_tool_download_disables_kh2_but_keeps_fields_and_kh1_available(self):
        self.app.setup_busy=True
        self.app.refresh_start_state()
        self.assertEqual(self.app.start_button.cget('state'),'disabled')
        self.assertEqual(self.app.romfs.entry.cget('state'),'normal')
        self.app.select_game('kh1');self.app.update()
        self.assertEqual(self.app.start_button.cget('state'),'normal')
        self.app.select_game('kh2');self.app.update()
        self.assertEqual(self.app.start_button.cget('state'),'disabled')

    def test_tool_download_progress_and_success_restore_ready_state(self):
        self.app.setup_busy=True
        self.app.tool_setup.events.put({'phase':'download','label':'FFmpeg herunterladen','value':42,'received':10*1024**2,'total':20*1024**2})
        self.app.poll_tool_setup();self.app.update()
        self.assertIn('10.0 / 20.0 MB',self.app.tool_status.cget('text'))
        self.assertEqual(self.app.progress_value,42)
        self.app.tool_setup.events.put({'phase':'finished','success':True,'downloaded':True})
        self.app.poll_tool_setup();self.app.update()
        self.assertTrue(self.app.tools_ready);self.assertFalse(self.app.setup_busy)
        self.assertEqual(self.app.start_button.cget('state'),'normal')
        self.assertIn('ready',self.app.tool_status.cget('text'))

    def test_tool_failure_offers_retry_and_keeps_reason_in_log(self):
        self.app.setup_busy=True
        self.app.tool_setup.events.put({'phase':'finished','success':False,'error':'Network is offline'})
        self.app.poll_tool_setup();self.app.update()
        self.assertFalse(self.app.tools_ready);self.assertFalse(self.app.setup_busy)
        self.assertTrue(self.app.tool_retry.winfo_ismapped())
        self.assertIn('Network is offline',self.app.log.get('1.0','end'))
        with mock.patch.object(self.app.tool_setup,'start') as start:
            self.app.begin_tool_setup()
        start.assert_called_once()
        self.assertTrue(self.app.setup_busy)
        self.assertFalse(self.app.tool_retry.winfo_ismapped())

    def test_background_tool_progress_preserves_running_patch_status(self):
        self.app.select_game('kh1')
        self.app.set_busy(True)
        self.app.status.config(text='Archiv bauen')
        self.app.progress_value=50
        self.app.tool_setup.events.put({'phase':'download','label':'FFmpeg herunterladen','value':15,'received':1024**2,'total':10*1024**2})
        self.app.poll_tool_setup()
        self.assertEqual(self.app.status.cget('text'),'Archiv bauen')
        self.assertEqual(self.app.progress_value,50)


if __name__=='__main__':unittest.main()
