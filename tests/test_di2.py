"""Run with:  python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from di2 import adjust, edit, framing, presets, settings  # noqa: E402
from di2.session import forbidden  # noqa: E402
from fake_bike import FakeBike, FakeSession, load_snapshot  # noqa: E402

adjust.time.sleep = lambda s: None          # no real waits in tests

RD, FD, LEFT = 4, 11, 19          # slots in the fixture snapshot


class Framing(unittest.TestCase):
    def test_roundtrip_with_escapes(self):
        frame = framing.encode(0x48, bytes([0xBB, 0xBD, 0x01]))
        self.assertEqual(frame[0], framing.FLAG)
        self.assertEqual(frame[-1], framing.FLAG)
        self.assertNotIn(framing.FLAG, frame[1:-1])
        (f,) = list(framing.Decoder().feed(frame))
        self.assertEqual((f.ctrl, f.data, f.fcs_ok), (0x48, bytes([0xBB, 0xBD, 0x01]), True))

    def test_known_frames(self):
        self.assertEqual(framing.encode(0x05).hex(' '), 'bb 05 fb bb')
        self.assertEqual(framing.encode(0x03, b'\x20').hex(' '), 'bb 03 20 dd bb')

    def test_split_input(self):
        dec, raw = framing.Decoder(), bytes.fromhex('bb2530010000aabb')
        self.assertEqual(list(dec.feed(raw[:3])), [])
        (f,) = list(dec.feed(raw[3:]))
        self.assertEqual(f.ctrl, 0x25)


class Guard(unittest.TestCase):
    def test_refuses_update_and_bootloader(self):
        self.assertTrue(forbidden(0x48, bytes([0, 0x32, 0x20, 1])))
        self.assertTrue(forbidden(0x88, b'\x01'))
        self.assertTrue(forbidden(0x03, b'\x40'))
        self.assertTrue(forbidden(0x48, bytes([RD, 0x0B, 0x20, 0, 0])))

    def test_allows_normal_traffic(self):
        for ctrl, data in [(0x03, b'\x00'), (0x48, bytes([0, 0x32, 0x10, 0, 25, 0, 0])),
                           (0x48, bytes([RD, 0x0B, 0x08, 5, 11, 0, 0])), (0x48, bytes([RD, 0x0B, 0x10, 1, 11]))]:
            self.assertIsNone(forbidden(ctrl, data))


class Edit(unittest.TestCase):
    def setUp(self):
        self.snap = load_snapshot()

    def test_multi_shift_off_needs_no_limit_change_when_unlimited(self):
        errors, warnings, out = edit.check(self.snap, [{'slot': RD, 'key': 'multi_shift_speed', 'value': 0}])
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual([c['key'] for c in out], ['multi_shift_speed'])

    def test_gear_limit_refused_while_multi_shift_off(self):
        errors, _, _ = edit.check(self.snap, [{'slot': RD, 'key': 'multi_shift_speed', 'value': 0},
                                              {'slot': 0, 'key': 'multi_shift_gear_limit', 'value': 2}])
        self.assertTrue(errors)

    def test_missing_shift_function_warns(self):
        _, warnings, _ = edit.check(self.snap, [{'slot': LEFT, 'key': 'button.X', 'value': 2}])
        self.assertTrue(any('front shift up' in w for w in warnings))

    def test_semi_steps_limited(self):
        errors, _, _ = edit.check(self.snap, [{'slot': FD, 'key': 'semi_steps.1.up', 'value': 9}])
        self.assertTrue(errors)

    def test_apply_writes_and_verifies(self):
        s = FakeSession()
        _, _, out = edit.check(self.snap, [{'slot': FD, 'key': 'synchro_shift_speed', 'value': 1},
                                           {'slot': RD, 'key': 'multi_shift_speed', 'value': 4}])
        report = edit.apply(s, self.snap, out)
        self.assertTrue(all(r['verified'] for r in report), report)
        self.assertEqual(s.bike.v['synchro_speed'], 1)
        self.assertEqual(s.bike.v['ms_speed'], 4)


class Adjust(unittest.TestCase):
    def test_cancel_restores_trim_and_mode(self):
        s = FakeSession()
        a = adjust.RearAdjust(s, RD, FD)
        a.prepare()
        a.start()
        self.assertEqual((s.bike.v['rd_gear'][0], s.bike.v['mode']), (5, 1))
        a.trim(+1)
        a.trim(+1)
        a.shift(+1)
        a.cancel()
        self.assertEqual((s.bike.v['trim'], s.bike.v['mode']), (0, 0))
        fd_shifts = [p[0] for slot, g, c, p in s.bike.sent if slot == FD and (g, c) == (0x0B, 0x08)]
        self.assertEqual(fd_shifts, [5, 6])          # every rear shift mirrored to the FD

    def test_set_trim_jumps_and_clamps(self):
        s = FakeSession()
        a = adjust.RearAdjust(s, RD, FD)
        a.prepare()
        a.start()
        a.set_trim(-3)
        self.assertEqual(s.bike.v['trim'], -3)
        a.set_trim(99)
        self.assertEqual(s.bike.v['trim'], s.bike.v['trim_range'])

    def test_finish_keeps_trim(self):
        s = FakeSession()
        a = adjust.RearAdjust(s, RD, FD)
        a.prepare()
        a.start()
        a.trim(-1)
        a.finish()
        self.assertEqual((s.bike.v['trim'], s.bike.v['mode']), (-1, 0))

    def test_trim_stays_in_range(self):
        s = FakeSession()
        a = adjust.RearAdjust(s, RD, FD)
        a.prepare()
        a.start()
        for _ in range(10):
            a.trim(+1)
        self.assertEqual(s.bike.v['trim'], s.bike.v['trim_range'])

    def test_battery_answering_wins_over_bcr2_power_state(self):
        s = FakeSession()
        s.battery_present = False                     # BCR2 says no battery, but it answers 100 %
        s.bike.v['soc'] = 100
        s.capacitor = [None]
        a = adjust.RearAdjust(s, RD, FD)
        a.prepare()
        self.assertEqual(a.power(), {'source': 'battery', 'level': 100})

    def test_waits_for_capacitor_to_recharge(self):
        s = FakeSession()
        s.battery_present = False                     # BCR2 powers the bus, no battery
        s.bike.v['soc'] = None
        s.capacitor = [2600, 2640, 2700]              # recharging
        a = adjust.RearAdjust(s, RD, FD)
        a.charge_interval = 0
        self.assertEqual(a.prepare()['gear'], 3)

    def test_gives_up_if_capacitor_never_recharges(self):
        s = FakeSession()
        s.battery_present = False
        s.bike.v['soc'] = None
        s.capacitor = [2600]
        a = adjust.RearAdjust(s, RD, FD)
        a.charge_interval, a.charge_polls = 0, 3
        with self.assertRaises(Exception):
            a.prepare()


class Presets(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        self.dir = tempfile.mkdtemp()
        os.environ['DI2_PRESETS'] = os.path.join(self.dir, 'presets.json')

    def test_save_load_delete(self):
        rd = {'model': 'RD-R8050-GS', 'serial': '000000000002', 'settings': {'trim_range': 16}}
        presets.save('Wheel', 4, rd)
        presets.save('Trainer', 0, rd)
        self.assertEqual({k: v['rear_trim'] for k, v in presets.load().items()}, {'Wheel': 4, 'Trainer': 0})
        presets.delete('Wheel')
        self.assertEqual(list(presets.load()), ['Trainer'])

    def test_apply_trim_sets_and_leaves_adjust_mode_without_shifting(self):
        s = FakeSession()
        a = adjust.RearAdjust(s, RD, FD)
        a.prepare()
        a.apply_trim(-2)
        self.assertEqual((s.bike.v['trim'], s.bike.v['mode'], s.bike.v['rd_gear'][0]), (-2, 0, 3))
        self.assertFalse(any((g, c) == (0x0B, 0x08) for _, g, c, _ in s.bike.sent))


class Settings(unittest.TestCase):
    def test_readers_decode_the_fake_bike(self):
        s = FakeSession(bike=FakeBike())
        fd = settings.front_derailleur(s, FD)
        self.assertEqual(fd['chainrings'], '52-36')
        self.assertEqual(fd['synchro mode 1 up']['type'], 'semi-synchronized')
        self.assertEqual(fd['synchro mode 2 up']['rd_shift_point'], 7)
        rd = settings.rear_derailleur(s, RD, gs=True)
        self.assertEqual((rd['cassette'], rd['multi_shift_speed']), ('11-28', 'normal'))
        self.assertEqual((rd['trim'], rd['trim_range']), (0, 4))


if __name__ == '__main__':
    unittest.main()
