import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import asr_vm


class LocalVmSafetyTests(unittest.TestCase):
    def test_default_network_is_restricted(self):
        args = asr_vm.start_args(Path('C:/qa'), False, False)
        net = args[args.index('-netdev') + 1]
        self.assertIn('restrict=on', net)
        self.assertIn('hostfwd=tcp:127.0.0.1:22293-:22', net)
        self.assertIn('hostfwd=tcp:127.0.0.1:18093-:80', net)

    def test_only_explicit_provisioning_enables_nat(self):
        args = asr_vm.start_args(Path('C:/qa'), True, True)
        self.assertIn('restrict=off', args[args.index('-netdev') + 1])

    def test_management_is_loopback_only(self):
        args = asr_vm.start_args(Path('C:/qa'), False, False)
        self.assertIn('tcp:127.0.0.1:49193,', args[args.index('-qmp') + 1])
        self.assertNotIn('0.0.0.0', ' '.join(args))

    def test_template_and_disposable_use_different_disks(self):
        base = asr_vm.start_args(Path('C:/qa'), True, False)
        test = asr_vm.start_args(Path('C:/qa'), False, False)
        self.assertIn('asr-qa-asl3-template.qcow2', base[base.index('-drive') + 1])
        self.assertIn('asr-qa-disposable.qcow2', test[test.index('-drive') + 1])

    def test_qmp_connection_errors_are_not_assumed_stopped(self):
        with patch.object(asr_vm, 'qmp', side_effect=RuntimeError('wrong instance')):
            with self.assertRaisesRegex(RuntimeError, 'wrong instance'):
                asr_vm.running()

    def test_absent_vm_is_stopped(self):
        with patch.object(asr_vm, 'qmp', side_effect=ConnectionRefusedError):
            self.assertIsNone(asr_vm.running())

    def test_unresponsive_management_does_not_allow_disk_changes(self):
        with patch.object(asr_vm, 'qmp', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                asr_vm.running()

    def test_seed_requires_a_dedicated_public_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ssh').mkdir()
            (root / 'ssh/id_ed25519.pub').write_text('not a key')
            with self.assertRaisesRegex(RuntimeError, 'public key'):
                asr_vm.create_seed(root, 'test')

    def test_seed_preserves_provisioned_asterisk_on_clone(self):
        import pycdlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ssh').mkdir()
            (root / 'ssh/id_ed25519.pub').write_text('ssh-ed25519 test')
            asr_vm.create_seed(root, 'test')
            iso = pycdlib.PyCdlib()
            iso.open(str(root / 'seed.iso'))
            output = io.BytesIO()
            iso.get_file_from_iso_fp(output, joliet_path='/user-data')
            iso.close()
            self.assertIn(b'test -f /root/asr-qa/asl-provisioned ||', output.getvalue())

    def test_ssh_requires_pinned_host_key(self):
        args = asr_vm.ssh_args(Path('C:/qa'))
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertEqual(args[-1], 'asrqa@127.0.0.1')


if __name__ == '__main__':
    unittest.main()
