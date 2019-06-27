# -*- mode: python; python-indent: 4 -*-
from ipaddress import IPv4Interface


def disable_ldp(root, igp_domain):
    connections = dict()

    def add_device_interface(device_name, is_netconf, interface_name,
                             address, netmask):
        entry = (device_name, is_netconf, interface_name)
        ip_network = IPv4Interface(unicode(address + '/' + netmask)).network
        if ip_network in connections:
            connections[ip_network].append(entry)
        else:
            connections[ip_network] = [entry]

    def delete_interface((device_name, is_netconf, interface_name)):
        device = root.devices.device[device_name]
        if is_netconf:
            ldp_interfaces = device.config.mpls_ldp_cfg__mpls_ldp.\
                             default_vrf.interfaces.interface
        else:
            ldp_interfaces = device.config.cisco_ios_xr__mpls.ldp.interface
        if interface_name in ldp_interfaces:
            del ldp_interfaces[interface_name]

    for router in igp_domain.router:
        device = root.devices.device[router.name]

        # NETCONF device
        if device.device_type.ne_type == 'netconf':
            for interface in device.config.ifmgr_cfg__interface_configurations.\
                             interface_configuration:
                addr = interface.ipv4_io_cfg__ipv4_network.addresses.primary
                add_device_interface(
                    device.name, True, interface.interface_name,
                    addr.address, addr.netmask)

        # CLI device
        else:
            for interface in device.config.cisco_ios_xr__interface.\
                             GigabitEthernet:
                addr = interface.ipv4.address
                add_device_interface(
                    device.name, False, 'GigabitEthernet%s' % interface.id,
                    addr.ip, addr.mask)

    for connection in connections.values():
        if len(connection) == 2:
            delete_interface(connection[0])
            delete_interface(connection[1])
