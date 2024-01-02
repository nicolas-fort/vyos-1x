#!/usr/bin/env python3
#
# Copyright (C) 2018-2023 VyOS maintainers and contributors
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
import re
from sys import exit

from vyos.base import Warning
from vyos.config import Config
from vyos.configverify import verify_interface_exists
from vyos.template import render
from vyos.utils.process import call
from vyos import ConfigError
from vyos import airbag
airbag.enable()

config_file = r'/run/ddclient/ddclient.conf'
systemd_override = r'/run/systemd/system/ddclient.service.d/override.conf'

# Dynamic interfaces that might not exist when the configuration is loaded
dynamic_interfaces = ('pppoe', 'sstpc')

# Protocols that require zone
zone_necessary = ['cloudflare', 'digitalocean', 'godaddy', 'hetzner', 'gandi',
                  'nfsn', 'nsupdate']
zone_supported = zone_necessary + ['dnsexit2', 'zoneedit1']

# Protocols that do not require username
username_unnecessary = ['1984', 'cloudflare', 'cloudns', 'digitalocean', 'dnsexit2',
                        'duckdns', 'freemyip', 'hetzner', 'keysystems', 'njalla',
                        'nsupdate', 'regfishde']

# Protocols that support TTL
ttl_supported = ['cloudflare', 'dnsexit2', 'gandi', 'hetzner', 'godaddy', 'nfsn',
                 'nsupdate']

# Protocols that support both IPv4 and IPv6
dualstack_supported = ['cloudflare', 'digitalocean', 'dnsexit2', 'duckdns',
                       'dyndns2', 'easydns', 'freedns', 'hetzner', 'infomaniak',
                       'njalla']

# dyndns2 protocol in ddclient honors dual stack for selective servers
# because of the way it is implemented in ddclient
dyndns_dualstack_servers = ['members.dyndns.org', 'dynv6.com']

def get_config(config=None):
    if config:
        conf = config
    else:
        conf = Config()

    base = ['service', 'dns', 'dynamic']
    if not conf.exists(base):
        return None

    dyndns = conf.get_config_dict(base, key_mangling=('-', '_'),
                                  no_tag_node_value_mangle=True,
                                  get_first_key=True,
                                  with_recursive_defaults=True)

    dyndns['config_file'] = config_file
    return dyndns

def verify(dyndns):
    # bail out early - looks like removal from running config
    if not dyndns or 'name' not in dyndns:
        return None

    # Dynamic DNS service provider - configuration validation
    for service, config in dyndns['name'].items():

        error_msg_req = f'is required for Dynamic DNS service "{service}"'
        error_msg_uns = f'is not supported for Dynamic DNS service "{service}"'

        for field in ['protocol', 'address', 'host_name']:
            if field not in config:
                raise ConfigError(f'"{field.replace("_", "-")}" {error_msg_req}')

        # If dyndns address is an interface, ensure
        # that the interface exists (or just warn if dynamic interface)
        # and that web-options are not set
        if config['address'] != 'web':
            # exclude check interface for dynamic interfaces
            if config['address'].startswith(dynamic_interfaces):
                Warning(f'Interface "{config["address"]}" does not exist yet and cannot '
                        f'be used for Dynamic DNS service "{service}" until it is up!')
            else:
                verify_interface_exists(config['address'])
            if 'web_options' in config:
                raise ConfigError(f'"web-options" is applicable only when using HTTP(S) '
                                  f'web request to obtain the IP address')

        # Warn if using checkip.dyndns.org, as it does not support HTTPS
        # See: https://github.com/ddclient/ddclient/issues/597
        if 'web_options' in config:
            if 'url' not in config['web_options']:
                raise ConfigError(f'"url" in "web-options" {error_msg_req} '
                                  f'with protocol "{config["protocol"]}"')
            elif re.search("^(https?://)?checkip\.dyndns\.org", config['web_options']['url']):
                Warning(f'"checkip.dyndns.org" does not support HTTPS requests for IP address '
                        f'lookup. Please use a different IP address lookup service.')

        # RFC2136 uses 'key' instead of 'password'
        if config['protocol'] != 'nsupdate' and 'password' not in config:
            raise ConfigError(f'"password" {error_msg_req}')

        # Other RFC2136 specific configuration validation
        if config['protocol'] == 'nsupdate':
            if 'password' in config:
                raise ConfigError(f'"password" {error_msg_uns} with protocol "{config["protocol"]}"')
            for field in ['server', 'key']:
                if field not in config:
                    raise ConfigError(f'"{field}" {error_msg_req} with protocol "{config["protocol"]}"')

        if config['protocol'] in zone_necessary and 'zone' not in config:
            raise ConfigError(f'"zone" {error_msg_req} with protocol "{config["protocol"]}"')

        if config['protocol'] not in zone_supported and 'zone' in config:
            raise ConfigError(f'"zone" {error_msg_uns} with protocol "{config["protocol"]}"')

        if config['protocol'] not in username_unnecessary and 'username' not in config:
            raise ConfigError(f'"username" {error_msg_req} with protocol "{config["protocol"]}"')

        if config['protocol'] not in ttl_supported and 'ttl' in config:
            raise ConfigError(f'"ttl" {error_msg_uns} with protocol "{config["protocol"]}"')

        if config['ip_version'] == 'both':
            if config['protocol'] not in dualstack_supported:
                raise ConfigError(f'Both IPv4 and IPv6 at the same time {error_msg_uns} '
                                  f'with protocol "{config["protocol"]}"')
            # dyndns2 protocol in ddclient honors dual stack only for dyn.com (dyndns.org)
            if config['protocol'] == 'dyndns2' and 'server' in config and config['server'] not in dyndns_dualstack_servers:
                raise ConfigError(f'Both IPv4 and IPv6 at the same time {error_msg_uns} '
                                  f'for "{config["server"]}" with protocol "{config["protocol"]}"')

        if {'wait_time', 'expiry_time'} <= config.keys() and int(config['expiry_time']) < int(config['wait_time']):
                raise ConfigError(f'"expiry-time" must be greater than "wait-time" for '
                                  f'Dynamic DNS service "{service}"')

    return None

def generate(dyndns):
    # bail out early - looks like removal from running config
    if not dyndns or 'name' not in dyndns:
        return None

    render(config_file, 'dns-dynamic/ddclient.conf.j2', dyndns, permission=0o600)
    render(systemd_override, 'dns-dynamic/override.conf.j2', dyndns)
    return None

def apply(dyndns):
    systemd_service = 'ddclient.service'
    # Reload systemd manager configuration
    call('systemctl daemon-reload')

    # bail out early - looks like removal from running config
    if not dyndns or 'name' not in dyndns:
        call(f'systemctl stop {systemd_service}')
        if os.path.exists(config_file):
            os.unlink(config_file)
    else:
        call(f'systemctl reload-or-restart {systemd_service}')

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
