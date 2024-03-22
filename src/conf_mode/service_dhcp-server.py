#!/usr/bin/env python3
#
# Copyright (C) 2018-2024 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from ipaddress import ip_address
from ipaddress import ip_network
from netaddr import IPAddress
from netaddr import IPRange
from sys import exit

from vyos.base import DeprecationWarning
from vyos.config import Config
from vyos.template import render
from vyos.utils.dict import dict_search
from vyos.utils.process import call
from vyos.utils.process import run
from vyos.utils.network import is_subnet_connected
from vyos.utils.network import is_addr_assigned
from vyos import ConfigError
from vyos import airbag
airbag.enable()

config_file = '/run/dhcp-server/dhcpd.conf'
systemd_override = r'/run/systemd/system/isc-dhcp-server.service.d/10-override.conf'

def dhcp_slice_range(exclude_list, range_dict):
    """
    This function is intended to slice a DHCP range. What does it mean?

    Lets assume we have a DHCP range from '192.0.2.1' to '192.0.2.100'
    but want to exclude address '192.0.2.74' and '192.0.2.75'. We will
    pass an input 'range_dict' in the format:
      {'start' : '192.0.2.1', 'stop' : '192.0.2.100' }
    and we will receive an output list of:
      [{'start' : '192.0.2.1' , 'stop' : '192.0.2.73'  },
       {'start' : '192.0.2.76', 'stop' : '192.0.2.100' }]
    The resulting list can then be used in turn to build the proper dhcpd
    configuration file.
    """
    output = []
    # exclude list must be sorted for this to work
    exclude_list = sorted(exclude_list)
    range_start = range_dict['start']
    range_stop = range_dict['stop']
    range_last_exclude = ''

    for e in exclude_list:
        if (ip_address(e) >= ip_address(range_start)) and \
           (ip_address(e) <= ip_address(range_stop)):
            range_last_exclude = e

    for e in exclude_list:
        if (ip_address(e) >= ip_address(range_start)) and \
           (ip_address(e) <= ip_address(range_stop)):

            # Build new address range ending one address before exclude address
            r = {
                'start' : range_start,
                'stop' : str(ip_address(e) -1)
            }
            # On the next run our address range will start one address after
            # the exclude address
            range_start = str(ip_address(e) + 1)

            # on subsequent exclude addresses we can not
            # append them to our output
            if not (ip_address(r['start']) > ip_address(r['stop'])):
                # Everything is fine, add range to result
                output.append(r)

            # Take care of last IP address range spanning from the last exclude
            # address (+1) to the end of the initial configured range
            if ip_address(e) == ip_address(range_last_exclude):
                r = {
                  'start': str(ip_address(e) + 1),
                  'stop': str(range_stop)
                }
                if not (ip_address(r['start']) > ip_address(r['stop'])):
                    output.append(r)
        else:
          # if the excluded address was not part of the range, we simply return
          # the entire ranga again
          if not range_last_exclude:
              if range_dict not in output:
                  output.append(range_dict)

    return output

def get_config(config=None):
    if config:
        conf = config
    else:
        conf = Config()
    base = ['service', 'dhcp-server']
    if not conf.exists(base):
        return None

    dhcp = conf.get_config_dict(base, key_mangling=('-', '_'),
                                no_tag_node_value_mangle=True,
                                get_first_key=True,
                                with_recursive_defaults=True)

    if 'shared_network_name' in dhcp:
        for network, network_config in dhcp['shared_network_name'].items():
            if 'subnet' in network_config:
                for subnet, subnet_config in network_config['subnet'].items():
                    # If exclude IP addresses are defined we need to slice them out of
                    # the defined ranges
                    if {'exclude', 'range'} <= set(subnet_config):
                        new_range_id = 0
                        new_range_dict = {}
                        for r, r_config in subnet_config['range'].items():
                            for slice in dhcp_slice_range(subnet_config['exclude'], r_config):
                                new_range_dict.update({new_range_id : slice})
                                new_range_id +=1

                        dhcp['shared_network_name'][network]['subnet'][subnet].update(
                                {'range' : new_range_dict})

    return dhcp

def verify(dhcp):
    # bail out early - looks like removal from running config
    if not dhcp or 'disable' in dhcp:
        return None

    # If DHCP is enabled we need one share-network
    if 'shared_network_name' not in dhcp:
        raise ConfigError('No DHCP shared networks configured.\n' \
                          'At least one DHCP shared network must be configured.')

    # Inspect shared-network/subnet
    listen_ok = False
    subnets = []
    failover_ok = False
    shared_networks =  len(dhcp['shared_network_name'])
    disabled_shared_networks = 0

    common_deprecation_msg = 'are subject of removal in VyOS 1.5! Please raise a feature request for proper CLI nodes!'
    if 'global_parameters' in dhcp:
        DeprecationWarning(f'Additional global parameters {common_deprecation_msg}')

    # A shared-network requires a subnet definition
    for network, network_config in dhcp['shared_network_name'].items():
        if 'shared_network_parameters' in network_config:
            DeprecationWarning(f'Additional shared network parameters in "{network}" {common_deprecation_msg}')

        if 'disable' in network_config:
            disabled_shared_networks += 1

        if 'subnet' not in network_config:
            raise ConfigError(f'No subnets defined for {network}. At least one\n' \
                              'lease subnet must be configured.')

        for subnet, subnet_config in network_config['subnet'].items():
            if 'subnet_parameters' in subnet_config:
                DeprecationWarning(f'Additional subnet parameters in "{subnet}" {common_deprecation_msg}')

            # All delivered static routes require a next-hop to be set
            if 'static_route' in subnet_config:
                for route, route_option in subnet_config['static_route'].items():
                    if 'next_hop' not in route_option:
                        raise ConfigError(f'DHCP static-route "{route}" requires router to be defined!')

            # DHCP failover needs at least one subnet that uses it
            if 'enable_failover' in subnet_config:
                if 'failover' not in dhcp:
                    raise ConfigError(f'Can not enable failover for "{subnet}" in "{network}".\n' \
                                      'Failover is not configured globally!')
                failover_ok = True

            # Check if DHCP address range is inside configured subnet declaration
            if 'range' in subnet_config:
                networks = []
                for range, range_config in subnet_config['range'].items():
                    if not {'start', 'stop'} <= set(range_config):
                        raise ConfigError(f'DHCP range "{range}" start and stop address must be defined!')

                    # Start/Stop address must be inside network
                    for key in ['start', 'stop']:
                        if ip_address(range_config[key]) not in ip_network(subnet):
                            raise ConfigError(f'DHCP range "{range}" {key} address not within shared-network "{network}, {subnet}"!')

                    # Stop address must be greater or equal to start address
                    if ip_address(range_config['stop']) < ip_address(range_config['start']):
                        raise ConfigError(f'DHCP range "{range}" stop address must be greater or equal\n' \
                                          'to the ranges start address!')

                    for network in networks:
                        start = range_config['start']
                        stop = range_config['stop']
                        if start in network:
                            raise ConfigError(f'Range "{range}" start address "{start}" already part of another range!')
                        if stop in network:
                            raise ConfigError(f'Range "{range}" stop address "{stop}" already part of another range!')

                    tmp = IPRange(range_config['start'], range_config['stop'])
                    networks.append(tmp)

            # Exclude addresses must be in bound
            if 'exclude' in subnet_config:
                for exclude in subnet_config['exclude']:
                    if ip_address(exclude) not in ip_network(subnet):
                        raise ConfigError(f'Excluded IP address "{exclude}" not within shared-network "{network}, {subnet}"!')

            # At least one DHCP address range or static-mapping required
            if 'range' not in subnet_config and 'static_mapping' not in subnet_config:
                raise ConfigError(f'No DHCP address range or active static-mapping configured\n' \
                                  f'within shared-network "{network}, {subnet}"!')

            if 'static_mapping' in subnet_config:
                # Static mappings require just a MAC address (will use an IP from the dynamic pool if IP is not set)
                used_ips = []
                used_mac = []
                for mapping, mapping_config in subnet_config['static_mapping'].items():
                    if 'ip_address' in mapping_config:
                        if ip_address(mapping_config['ip_address']) not in ip_network(subnet):
                            raise ConfigError(f'Configured static lease address for mapping "{mapping}" is\n' \
                                              f'not within shared-network "{network}, {subnet}"!')

                        if 'mac_address' not in mapping_config:
                            raise ConfigError(f'MAC address required for static mapping "{mapping}"\n' \
                                              f'within shared-network "{network}, {subnet}"!')

                        if 'disable' not in mapping_config:
                            if mapping_config['ip_address'] in used_ips:
                                raise ConfigError(f'Configured IP address for static mapping "{mapping}" already exists on another static mapping')
                            used_ips.append(mapping_config['ip_address'])

                    if 'mac_address' in mapping_config and 'disable' not in mapping_config:
                        if mapping_config['mac_address'] in used_mac:
                            raise ConfigError(f'Configured MAC address for static mapping "{mapping}" already exists on another static mapping')
                        used_mac.append(mapping_config['mac_address'])

            # There must be one subnet connected to a listen interface.
            # This only counts if the network itself is not disabled!
            if 'disable' not in network_config:
                if is_subnet_connected(subnet, primary=False):
                    listen_ok = True

            # Subnets must be non overlapping
            if subnet in subnets:
                raise ConfigError(f'Configured subnets must be unique! Subnet "{subnet}"\n'
                                   'defined multiple times!')
            subnets.append(subnet)

            # Check for overlapping subnets
            net = ip_network(subnet)
            for n in subnets:
                net2 = ip_network(n)
                if (net != net2):
                    if net.overlaps(net2):
                        raise ConfigError(f'Conflicting subnet ranges: "{net}" overlaps "{net2}"!')

    # Prevent 'disable' for shared-network if only one network is configured
    if (shared_networks - disabled_shared_networks) < 1:
        raise ConfigError(f'At least one shared network must be active!')

    if 'failover' in dhcp:
        if not failover_ok:
            raise ConfigError('DHCP failover must be enabled for at least one subnet!')

        for key in ['name', 'remote', 'source_address', 'status']:
            if key not in dhcp['failover']:
                tmp = key.replace('_', '-')
                raise ConfigError(f'DHCP failover requires "{tmp}" to be specified!')

        if 'split' in dhcp['failover'] and dhcp['failover']['status'] != 'primary':
            raise ConfigError('DHCP failover split parameter can only be used if status is primary')

    for address in (dict_search('listen_address', dhcp) or []):
        if is_addr_assigned(address):
            listen_ok = True
            # no need to probe further networks, we have one that is valid
            continue
        else:
            raise ConfigError(f'listen-address "{address}" not configured on any interface')


    if not listen_ok:
        raise ConfigError('None of the configured subnets have an appropriate primary IP address on any\n'
                          'broadcast interface configured, nor was there an explicit listen-address\n'
                          'configured for serving DHCP relay packets!')

    return None

def generate(dhcp):
    # bail out early - looks like removal from running config
    if not dhcp or 'disable' in dhcp:
        return None

    # Please see: https://vyos.dev/T1129 for quoting of the raw
    # parameters we can pass to ISC DHCPd
    tmp_file = '/tmp/dhcpd.conf'
    render(tmp_file, 'dhcp-server/dhcpd.conf.j2', dhcp,
           formater=lambda _: _.replace("&quot;", '"'))
    # XXX: as we have the ability for a user to pass in "raw" options via VyOS
    # CLI (see T3544) we now ask ISC dhcpd to test the newly rendered
    # configuration
    tmp = run(f'/usr/sbin/dhcpd -4 -q -t -cf {tmp_file}')
    if tmp > 0:
        if os.path.exists(tmp_file):
            os.unlink(tmp_file)
        raise ConfigError('Configuration file errors encountered - check your options!')

    # Now that we know that the newly rendered configuration is "good" we can
    # render the "real" configuration
    render(config_file, 'dhcp-server/dhcpd.conf.j2', dhcp,
           formater=lambda _: _.replace("&quot;", '"'))
    render(systemd_override, 'dhcp-server/10-override.conf.j2', dhcp)

    # Clean up configuration test file
    if os.path.exists(tmp_file):
        os.unlink(tmp_file)

    return None

def apply(dhcp):
    call('systemctl daemon-reload')
    # bail out early - looks like removal from running config
    if not dhcp or 'disable' in dhcp:
        call('systemctl stop isc-dhcp-server.service')
        if os.path.exists(config_file):
            os.unlink(config_file)

        return None

    call('systemctl restart isc-dhcp-server.service')
    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
