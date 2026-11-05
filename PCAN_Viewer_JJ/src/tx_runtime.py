"""Main TX payload state, without Qt or GUI-thread work on the send path."""
import copy
import threading

import can

from src.crc_utils import calculate_crc16_ccitt_false
from src.db_frame_format import frame_is_extended
from src.tx_counter import counter_payload, latest_rx_payload
from src.tx_schedule import bus_schedule


class TxRuntime:
    def __init__(self, packet, panel, states):
        self.packet = copy.deepcopy(packet)
        if self.packet.get('crc_type') == 'Hyundai_CRC':
            self.packet.setdefault('alive_counter', 0)
        self.bus = panel.buses[packet['bus']]
        self.main = panel.main_window
        self.message = panel.db_messages.get(packet['bus'], {}).get(packet['id'])
        self.states = copy.deepcopy(states)
        self.lock = threading.RLock()
        self.schedule = bus_schedule(self.main, packet['bus'])
        self.sent = 0
        self.last_payload = bytes(packet['data'])
        self.is_extended = frame_is_extended(
            packet['id'], packet.get('is_extended_id'), self.message)

    def send(self, record=False):
        with self.lock, self.schedule.lock:
            p = self.packet
            payload = bytes(p['data'])
            states = {}
            if p.get('signal_counters'):
                received = latest_rx_payload(self.main, p['bus'], p['id'])
                payload, states = counter_payload(
                    self.message, payload, p['signal_counters'], self.states,
                    received_payload=received)
            alive = p.get('alive_counter', 0)
            if p.get('crc_type') == 'Hyundai_CRC':
                body = bytes([alive]) + payload[3:]
                crc = calculate_crc16_ccitt_false(body + (0xF800 + p['id']).to_bytes(2, 'little'))
                payload = (crc.to_bytes(2, 'little') + body).ljust(p['length'], b'\0')[:p['length']]
            frame = can.Message(arbitration_id=p['id'], data=payload,
                                is_extended_id=self.is_extended, is_fd=p['is_fd'],
                                bitrate_switch=p.get('is_brs', False))
            self.bus.send(frame)
            # Commit counters only after the driver accepted the frame.
            self.states = states
            if p.get('crc_type') == 'Hyundai_CRC':
                p['alive_counter'] = (alive + 1) % 256
            self.sent += 1
            self.last_payload = payload
            return payload
