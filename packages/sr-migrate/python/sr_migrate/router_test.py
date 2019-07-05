# -*- mode: python; python-indent: 4 -*-
from contextlib import contextmanager
import functools
import traceback
import ncs
from ncs.application import PlanComponent
from sr_migrate.ios_xr_exec import IosXrExec


def to_result(test):
    if test:
        return 'PASS'
    return 'FAIL'

def get_host_name(device):
    if device.device_type.ne_type == 'netconf':
        return device.config.shellutil_cfg__host_names.host_name

    return device.config.cisco_ios_xr__hostname

def continue_on_error(test_name):
    def decorator(function):
        @functools.wraps(function)
        def wrapper(self, *args, **kwargs):
            try:
                return function(self, *args, **kwargs)
            except (AttributeError, KeyError):
                with self.write_result() as result:
                    ncs.maagic.get_trans(result).set_elem(
                        'ERROR', '%s/%s-test-result' % (result._path,
                                                        test_name))
                self.log.error('Exception running %s test between %s --> %s'
                               % (test_name, self.router_name,
                                  self.current_destination))
                self.log.error(traceback.format_exc())
        return wrapper
    return decorator

class RouterTest(object):
    def __init__(self, log, router, options, result, destination_routers):
        self.maapi = None
        self.device_exec = None
        self.log = log
        self.router_name = router.name
        self.options = options

        igp_domain = ncs.maagic.cd(router, '..')
        self.igp_domain = (igp_domain.name, igp_domain.loopback,
                           igp_domain.address_family)

        self.routers_and_plans = dict(
            (router.name, self.setup_plan(result, router))
            for router in destination_routers)

        self.result_path = result.router.create(router.name)._path
        self.current_destination = None

    def get_loopback_ip_address(self, device):
        (__, loopback, address_family) = self.igp_domain
        if device.device_type.ne_type == 'netconf':
            interface = device.config.ifmgr_cfg__interface_configurations.\
                   interface_configuration['act', 'Loopback%d' % loopback]

            if address_family == 'ipv4':
                return interface.ipv4_network.addresses.primary.address

            try:
                return next(iter(interface.ipv6_network.addresses.
                                 regular_addresses.regular_address)).address
            except StopIteration:
                raise AttributeError(
                    'No IPV6 loopback regular address configured on ',
                    'NETCONF device %s' % device.name)

        interface = device.config.cisco_ios_xr__interface.Loopback[loopback]
        if address_family == 'ipv4':
            return interface.ipv4.address.ip

        try:
            return next(iter(interface.ipv6.address.prefix_list)).prefix
        except StopIteration:
            raise AttributeError('No IPV6 loopback address prefix ',
                                 'configured on CLI device %s' % device.name)

    def get_prefix_sid(self, device):
        (instance, loopback, address_family) = self.igp_domain
        if device.device_type.ne_type == 'netconf':
            return device.config.clns_isis_cfg__isis.\
                   instances.instance[instance].\
                   interfaces.interface['Loopback%d' % loopback].\
                   interface_afs.interface_af[address_family, 'unicast'].\
                   interface_af_data.prefix_sid.value

        interface = device.config.cisco_ios_xr__router.isis.tag[instance].\
                    interface['Loopback%d' % loopback]

        return interface.address_family.ipv4.unicast.prefix_sid.absolute if (
            address_family == 'ipv4') else (
                interface.address_family.ipv6.unicast.prefix_sid.absolute)

    def setup_plan(self, result, router):
        test_plan = PlanComponent(
            result, '%s --> %s' % (self.router_name, router.name),
            'destination-test')
        (__, do_cef_validation, do_ping_test) = self.options
        test_plan.append_state('adjacency-sids-validation')
        test_plan.append_state('prefix-sid-validation')
        if do_cef_validation:
            test_plan.append_state('cef-validation')

        if do_ping_test:
            test_plan.append_state('ping-test')
        return test_plan.component._path

    def run(self):
        with ncs.maapi.Maapi() as maapi:
            with ncs.maapi.Session(maapi, 'admin', 'python'):
                self.maapi = maapi
                self.device_exec = IosXrExec(maapi, self.log, self.router_name)
                if not hasattr(self.device_exec, 'device'):
                    return True

                result = None
                router_test = False

                (isis_instance, __, __) = self.igp_domain
                try:
                    neighbours = self.device_exec.\
                                 get_isis_neighbors(isis_instance)
                except AttributeError:
                    self.log.error('Exception getting router %s neighbours'
                                   % self.router_name)
                    self.log.error(traceback.format_exc())
                    result = 'ERROR'

                if not result:
                    with self.maapi.start_read_trans() as th:
                        root = ncs.maagic.get_root(th)
                        router_test = all([
                            self.destination_test(
                                root.devices.device[router], neighbours)
                            for router in self.routers_and_plans])
                    result = to_result(router_test)

                with self.maapi.start_write_trans() as th:
                    test_result = ncs.maagic.get_node(th, self.result_path)
                    test_result.router_test_result = result
                    th.apply()

                return router_test

    def update_plan(self, node, plan_state, result):
        plan_path = self.routers_and_plans[self.current_destination]
        plan = ncs.maagic.cd(node, '%s/state{%s}' % (plan_path, plan_state))
        plan.status = 'reached' if result else 'failed'

    @contextmanager
    def write_result(self, plan_state=None, result=None):
        result_path = '%s/destination-router{%s}' % (self.result_path,
                                                     self.current_destination)
        with self.maapi.start_write_trans() as th:
            if not th.exists(result_path):
                th.create(result_path)

            node = ncs.maagic.get_node(th, result_path)
            if plan_state:
                self.update_plan(node, plan_state, result)

            try:
                yield node
            finally:
                th.apply()

    @continue_on_error('destination')
    def destination_test(self, device, neighbors):
        self.current_destination = device.name
        plan_path = self.routers_and_plans[device.name]

        system_id = get_host_name(device)
        prefix_sid = str(self.get_prefix_sid(device))
        address = self.get_loopback_ip_address(device)

        with self.write_result() as result:
            result.system_id = system_id
            result.prefix = address
            result.prefix_sid = prefix_sid

            if system_id in neighbors:
                result.is_neighbour = True
            else:
                result.is_neighbour = False
                del ncs.maagic.cd(result, '%s/state' % plan_path)\
                    ['adjacency-sids-validation']

        test_result = True
        if system_id in neighbors:
            test_result = self.adjacency_sids_validation(system_id)

        test_result = all([
            self.prefix_sid_validation(prefix_sid),
            self.cef_validation(address, prefix_sid),
            self.ping_test(address)
        ]) and test_result

        with self.write_result() as result:
            result.destination_test_result = to_result(test_result)

        return test_result

    @continue_on_error('adjacency-sids')
    def adjacency_sids_validation(self, system_id):
        (ensure_sid_protected, __, __) = self.options
        adjacency_sids = self.device_exec.get_adjacency_sids(system_id)
        all_adj_sids_check = bool(adjacency_sids)

        with self.write_result() as result:
            details = result.adjacency_sids_test_details
            details.has_adjacency_sids = bool(adjacency_sids)

            for (adj_sid, non_frr_adj_sid, interface) in adjacency_sids:
                adj_sid_check = all(self.device_exec.check_adjacency_sid_labels(
                    non_frr_adj_sid, interface))
                all_adj_sids_check = all_adj_sids_check and adj_sid_check

                adj_sid_result = details.adjacency_sids.create(non_frr_adj_sid)
                adj_sid_result.is_protected = False
                adj_sid_result.interface = interface
                adj_sid_result.mpls_forwarding_entry_valid = adj_sid_check

                is_protected = adj_sid.find('protected') != -1
                if is_protected or ensure_sid_protected:
                    adj_sid_check = all(self.device_exec.\
                         check_adjacency_sid_labels(adj_sid[:5], interface))
                    all_adj_sids_check = (all_adj_sids_check and adj_sid_check
                                          and is_protected)

                    adj_sid_result = details.adjacency_sids.create(adj_sid[:5])
                    adj_sid_result.is_protected = is_protected
                    adj_sid_result.interface = interface
                    adj_sid_result.mpls_forwarding_entry_valid = adj_sid_check

            result.adjacency_sids_test_result = to_result(all_adj_sids_check)
            self.update_plan(result, 'adjacency-sids-validation',
                             all_adj_sids_check)
        return all_adj_sids_check

    @continue_on_error('prefix-sid')
    def prefix_sid_validation(self, prefix_sid):
        sr_label_check = self.device_exec.check_isis_sr_label_table(prefix_sid)
        mpls_label_check = all(self.device_exec.\
                               check_prefix_sid_labels(prefix_sid))
        prefix_sid_check = sr_label_check and mpls_label_check

        with self.write_result('prefix-sid-validation',
                               prefix_sid_check) as result:
            details = result.prefix_sid_test_details
            details.has_prefix_sid = sr_label_check
            details.mpls_forwarding_entry_valid = mpls_label_check
            result.prefix_sid_test_result = to_result(prefix_sid_check)

        return prefix_sid_check

    @continue_on_error('cef')
    def cef_validation(self, prefix, prefix_sid):
        (__, do_cef_validation, __) = self.options
        if not do_cef_validation:
            return True

        (has_prefix_sid_label, has_label_imposition) = self.device_exec.\
            check_cef(prefix, prefix_sid)
        cef_check = has_prefix_sid_label and has_label_imposition

        with self.write_result('cef-validation', cef_check) as result:
            details = result.cef_test_details
            details.has_prefix_sid_label = has_prefix_sid_label
            details.has_label_imposition = has_label_imposition
            result.cef_test_result = to_result(cef_check)

        return cef_check

    @continue_on_error('ping')
    def ping_test(self, address):
        (__, __, do_ping_test) = self.options
        if not do_ping_test:
            return True

        ping_test = self.device_exec.ping(address)
        with self.write_result('ping-test', ping_test) as result:
            result.ping_test_result = to_result(ping_test)
        return ping_test
