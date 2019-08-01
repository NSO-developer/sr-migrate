# -*- mode: python; python-indent: 4 -*-
import re
import ncs
from sr_migrate.utils import parse_table


def get_cli_device(root, device_name):
    device = root.devices.device[device_name]
    if device.address == '127.0.0.1': #Netsim
        device = None
    elif device.device_type.ne_type != 'cli':
        device = next((cli_device for cli_device in root.devices.device
                       if cli_device.address == device.address and
                       cli_device.device_type.ne_type == 'cli'), None)
    return device

class IosXrExec(object):
    NEIGHBORS_TABLE_PATTERN = re.compile(
        r'.*^IS-IS \S* neighbors:\r\n(.*)^\r\nTotal neighbor count: \d+.*',
        re.DOTALL | re.MULTILINE)

    ADJACENCIES_TABLE_PATTERN = re.compile(
        r'.*^IS-IS \S* Level-1 adjacencies:\r\n(.*)^\r\
IS-IS \S* Level-2 adjacencies:\r\n(.*)^\r\nTotal adjacency count: \d+.*',
        re.DOTALL | re.MULTILINE)

    ISIS_SR_LABEL_TABLE_PATTERN = re.compile(
        r'.*\r\nIS-IS \S* IS Label Table\r\n(.*)\r\n[^\n]*$', re.DOTALL)

    MPLS_LABEL_TABLE_PATTERN = re.compile(
        r'.*\r\n(Local.*)\r\n[^\n]*$', re.DOTALL)

    PING_SUCCESS = re.compile(
        r'.*^!!!!!\r\nSuccess rate is 100 percent \(5\/5\)',
        re.MULTILINE | re.DOTALL)

    CEF_LINE_PATTERN = re.compile(
        r'^ *local label ([^ ]+) *labels imposed {([^}]*)}.*')

    def __init__(self, maapi, log, device_name):
        self.log = log

        with maapi.start_read_trans() as th:
            cli_device = get_cli_device(ncs.maagic.get_root(th), device_name)

        if cli_device:
            self.device = ncs.maagic.get_root(maapi).\
                          devices.device[cli_device.name]
            self.action = self.device.live_status.cisco_ios_xr_stats__exec.any
            self.input = self.action.get_input()

    def _exec(self, action_string):
        self.log.info(action_string)
        self.input.args = ['%s | noprompts ' % action_string]
        output = self.action(self.input)
        self.log.info(output.result)
        return output.result

    def ping(self, address):
        result = self._exec('ping sr-mpls %s/32 fec-type igp isis' % address)
        return bool(self.PING_SUCCESS.match(result))

    def get_isis_neighbors(self, instance_id):
        headers = ['System Id', 'Interface', 'SNPA', 'State', 'Holdtime',
                   'Type', 'IETF-NSF']
        result = self._exec('show isis instance %s neighbors' % instance_id)
        table_string = self.NEIGHBORS_TABLE_PATTERN.match(result).group(1)
        table = parse_table(table_string, headers)
        return set(neighbor['System Id'] for neighbor in table)

    def check_isis_sr_label_table(self, prefix_sid):
        headers = ['Label', 'Prefix/Interface']
        result = self._exec('show isis segment-routing label table')
        table_string = self.ISIS_SR_LABEL_TABLE_PATTERN.match(result).group(1)
        table = parse_table(table_string, headers)
        return prefix_sid in set(label['Label'] for label in table)

    def get_adjacency_sids(self, system_id):
        headers = ['System Id', 'Interface', 'SNPA', 'State', 'Hold',
                   'Changed', 'NSF', 'IPv4 BFD', 'IPv6 BFD']
        detailed_headers = ['Adjacency SID', 'Non-FRR Adjacency SID']

        result = self._exec('show isis adjacency systemid %s detail'
                            % system_id)
        table_strings = self.ADJACENCIES_TABLE_PATTERN.match(result).groups()

        return [(adjacency['Adjacency SID'],
                 adjacency['Non-FRR Adjacency SID'],
                 adjacency['Interface'])
                for table_string in table_strings
                for adjacency in parse_table(table_string, headers,
                                             detailed_headers)
                if ('Adjacency SID' in adjacency and
                    'Non-FRR Adjacency SID' in adjacency)]

    def get_mpls_forwarding_labels(self, label):
        headers = ['Local Label', 'Outgoing Label', 'Prefix or ID',
                   'Outgoing Interface', 'Next Hop', 'Bytes Switched']
        result = self._exec('show mpls forwarding labels %s' % label)
        table_string = self.MPLS_LABEL_TABLE_PATTERN.match(result)
        if table_string:
            return parse_table(table_string.group(1), headers)
        return []

    def check_sid_labels(self, sid, number_of_params,
                         primary_check_fn, addn_check_fn=None):
        result = tuple(False for _ in range(number_of_params))
        check_label = False
        addn_row_check = False
        for label in self.get_mpls_forwarding_labels(sid):
            if label['Local Label'] == sid:
                result = tuple(True for _ in range(number_of_params))
                check_label = True
            elif label['Local Label'] != '':
                check_label = False
                addn_row_check = False
            elif check_label and addn_check_fn:
                addn_row_check = True

            if check_label:
                result = tuple(
                    all(check) for check in zip(
                        result, addn_check_fn(label)
                        if addn_row_check else primary_check_fn(label)))
        return result

    def check_adjacency_sid_labels(self, adjacency_sid, interface):
        def check_label(label):
            return (True,
                    label['Outgoing Label'] == 'Pop',
                    label['Prefix or ID'][:6] == 'SR Adj',
                    label['Outgoing Interface'] == interface)
        def check_addn_row(label):
            return (True,
                    label['Outgoing Label'] != 'Unlabelled',
                    label['Prefix or ID'][:6] == 'SR Adj',
                    True)
        return self.check_sid_labels(adjacency_sid, 4,
                                     check_label, check_addn_row)

    def check_prefix_sid_labels(self, prefix_sid):
        def check_label(label):
            return (True,
                    label['Outgoing Label'] != 'Unlabelled',
                    label['Prefix or ID'][:6] == 'SR Pfx')
        return self.check_sid_labels(prefix_sid, 3, check_label)

    def check_cef(self, prefix, prefix_sid):
        result = self._exec('show cef %s' % prefix)
        cef_check = [match.groups() for match in
                     (self.CEF_LINE_PATTERN.match(line)
                      for line in result.splitlines())
                     if match]
        return (
            all(local_label == prefix_sid for (local_label, __) in cef_check),
            all(labels_imposed != 'None' for (__, labels_imposed)
                in cef_check)) if cef_check else (False, False)
