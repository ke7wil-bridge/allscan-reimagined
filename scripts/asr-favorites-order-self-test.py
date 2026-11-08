#!/usr/bin/env python3
"""Lossless ordering and conflict regression fixtures; never touch live files."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('favorites_manager', Path(__file__).with_name('asr-favorites-manager.py'))
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class FavoritesOrderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / 'favorites-test.ini'
        self.state = {'files': {self.file.name: {
            'order': ['22222', '11111'],
            'nodes': {'11111': {'description': 'Keep my description', 'color': '#123456'}}
        }}}

    def fixture(self, newline='\n', terminated=False):
        content = newline.join([
            '; Keep header comment', '[favorites]',
            'label[] = "First 11111"', 'cmd[] = "rpt cmd %node% ilink 3 11111"', '',
            'label[] = "Second 22222"', 'cmd[] = "rpt cmd %node% ilink 2 22222"',
        ]) + (newline if terminated else '')
        self.file.write_bytes(content.encode())
        return content

    def test_unterminated_last_command_does_not_merge_with_next_label(self):
        for newline in ('\n', '\r\n'):
            for terminated in (False, True):
                with self.subTest(newline=repr(newline), terminated=terminated):
                    original = self.fixture(newline, terminated)
                    before = manager.parse_allscan(original)
                    result = manager.upstream_export_preview(self.file, self.state)['content']
                    self.assertEqual(manager.parse_allscan(result), list(reversed(before)))
                    self.assertEqual(result.endswith(newline), terminated)
                    self.assertTrue(result.startswith('; Keep header comment' + newline + '[favorites]' + newline))
                    self.assertEqual(self.file.read_bytes(), original.encode(), 'Preview modified the source')
                    self.assertEqual(self.state['files'][self.file.name]['nodes']['11111']['color'], '#123456')

    def test_file_or_metadata_changes_invalidate_preview(self):
        self.fixture()
        initial = manager.upstream_export_preview(self.file, self.state)['token']
        self.file.write_bytes(self.file.read_bytes() + b'\n')
        self.assertNotEqual(initial, manager.upstream_export_preview(self.file, self.state)['token'])
        initial = manager.upstream_export_preview(self.file, self.state)['token']
        self.state['files'][self.file.name]['nodes']['11111']['color'] = '#654321'
        self.assertNotEqual(initial, manager.upstream_export_preview(self.file, self.state)['token'])

    def test_ambiguous_comments_or_sections_fail_without_writing(self):
        for separator in ('; Belongs to a station\n', '[other-section]\n'):
            original = self.fixture().replace('\n\nlabel', '\n' + separator + '\nlabel')
            self.file.write_bytes(original.encode())
            with self.assertRaisesRegex(ValueError, 'Comments or sections'):
                manager.upstream_export_preview(self.file, self.state)
            self.assertEqual(self.file.read_bytes(), original.encode())

    def test_duplicate_nodes_are_not_silently_collapsed(self):
        self.file.write_bytes(self.fixture().replace('22222', '11111').encode())
        with self.assertRaisesRegex(ValueError, 'membership differs or contains duplicates'):
            manager.upstream_export_preview(self.file, self.state)

    def test_confirmed_export_creates_exact_original_backup(self):
        original = self.fixture()
        token = manager.upstream_export_preview(self.file, self.state)['token']
        args = SimpleNamespace(action='export-upstream-order', file=str(self.file), value=token)
        with patch.object(manager, 'validate_favorites', return_value=self.file), \
                patch.object(manager, 'read_state', side_effect=lambda: json.loads(json.dumps(self.state))):
            result = manager.manage(args)
        self.assertTrue(result['changed'])
        self.assertEqual(Path(result['backup']).read_bytes(), original.encode())
        self.assertEqual([row['node'] for row in manager.parse_allscan(self.file.read_text())], ['22222', '11111'])

    def test_stale_confirmation_never_overwrites_external_edit(self):
        self.fixture()
        token = manager.upstream_export_preview(self.file, self.state)['token']
        external = self.file.read_bytes() + b'\n; Added elsewhere\n'
        self.file.write_bytes(external)
        args = SimpleNamespace(action='export-upstream-order', file=str(self.file), value=token)
        with patch.object(manager, 'validate_favorites', return_value=self.file), \
                patch.object(manager, 'read_state', return_value=self.state):
            with self.assertRaisesRegex(ValueError, 'changed since preview'):
                manager.manage(args)
        self.assertEqual(self.file.read_bytes(), external)
        self.assertEqual(list(self.file.parent.glob('*.asr-backup-*')), [])


if __name__ == '__main__':
    unittest.main()
