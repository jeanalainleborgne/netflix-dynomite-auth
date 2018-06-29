#!/usr/bin/env python3
from collections import namedtuple
from contextlib import ExitStack
from contextlib import contextmanager
from signal import SIGINT
from tempfile import mkdtemp
from time import sleep
from urllib.request import urlopen
import argparse
import json
import random
import socket
import sys
import yaml

from plumbum import FG
from plumbum import BG
from plumbum import LocalPath
from plumbum import local

from ip_util import quad2int
from ip_util import int2quad
from dyno_cluster import DynoCluster
from dyno_node import DynoNode
from func_test import comparison_test
from redis_node import RedisNode

DYN_O_MITE_DEFAULTS = dict(
    secure_server_option='datacenter',
    pem_key_file='conf/dynomite.pem',
    data_store=0,
    datastore_connections=1,
)
INTERNODE_LISTEN = 8101
CLIENT_LISTEN = 8102
REDIS_PORT = 1212
STATS_PORT = 22222
BASE_IPADDRESS = quad2int('127.0.1.1')
RING_SIZE = 2**32

SETTLE_TIME = 5

redis = local.get('./test/_binaries/redis-server', 'redis-server')
dynomite = local.get('./test/_binaries/dynomite', 'src/dynomite')

@contextmanager
def launch_redis(ip):
    logfile = 'logs/redis_{}.log'.format(ip)
    f = (redis['--bind', ip, '--port', REDIS_PORT] > logfile) & BG(-9)
    try:
        yield RedisNode(ip, REDIS_PORT)
    finally:
        f.proc.kill()
        f.wait()

def pick_tokens(count, start_offset):
    stride = RING_SIZE // count
    token = start_offset
    for i in range(count):
        yield token % RING_SIZE
        token += stride

def tokens_for_rack(count):
    offset = random.randrange(0, RING_SIZE)
    return list(pick_tokens(count, offset))

def tokens_for_dc(racks):
    return [
        (name, tokens_for_rack(count))
        for name, count in racks
    ]

def tokens_for_cluster(dcs, seed):
    if seed is not None:
        random.seed(seed)

    return [
        (dc['name'], tokens_for_dc(dc['racks']))
        for dc in dcs
    ]


def dc_count(dc):
    return sum(count for rack, count in dc)

def generate_ips():
    state = BASE_IPADDRESS
    while True:
        yield int2quad(state)
        state += 1

class DynoSpec(namedtuple('DynoSpec', 'ip port dc rack token '
    'local_connections remote_connections seed_string')):
    """Specifies how to launch a dynomite node"""

    def __new__(cls, ip, port, rack, dc, token, local_connections,
                remote_connections):
        seed_string = '{}:{}:{}:{}:{}'.format(ip, port, rack, dc, token)
        return super(DynoSpec, cls).__new__(cls, ip, port, rack, dc, token,
            local_connections, remote_connections, seed_string)

    def _generate_config(self, seeds_list):
        conf = dict(DYN_O_MITE_DEFAULTS)
        conf['datacenter'] = self.dc
        conf['rack'] = self.rack
        dyn_listen = '{}:{}'.format(self.ip, self.port)
        conf['dyn_listen'] = dyn_listen
        conf['listen'] = '{}:{}'.format(self.ip, CLIENT_LISTEN)

        # filter out our own seed string
        conf['dyn_seeds'] = [s for s in seeds_list if s != self.seed_string]
        conf['servers'] = ['{}:{}:0'.format(self.ip, REDIS_PORT)]
        conf['stats_listen'] = '{}:{}'.format(self.ip, STATS_PORT)
        conf['tokens'] = self.token
        conf['local_peer_connections'] = self.local_connections
        conf['remote_peer_connections'] = self.remote_connections
        return dict(dyn_o_mite=conf)

    def write_config(self, seeds_list):
        config = self._generate_config(seeds_list)
        filename = 'conf/{}:{}:{}.yml'.format(self.dc, self.rack, self.token)
        with open(filename, 'w') as fh:
            yaml.dump(config, fh, default_flow_style=False)
        return filename

    @contextmanager
    def launch(self, seeds_list):
        config_filename = self.write_config(seeds_list)

        with launch_redis(self.ip):
            logfile = 'logs/dynomite_{}.log'.format(self.ip)
            # Add '-v', '11' for maximum verbosity in logs
            # Dynomite exits with status 1 on SIGINT, this is what we expect.
            dynomite_future = dynomite['-v', '11', '-o', logfile, '-c', config_filename,
                '-v6'] & BG(1)

            try:
                yield DynoNode(self.ip)
            finally:
                dynomite_future.proc.send_signal(SIGINT)
                return_code= dynomite_future.wait()


@contextmanager
def launch_dynomite(nodes):
    seeds_list = [n.seed_string for n in nodes]
    launched_nodes = [DynoNode(n.ip) for n in nodes]
    with ExitStack() as stack:
        launched_nodes = [
            stack.enter_context(n.launch(seeds_list))
            for n in nodes
        ]

        yield DynoCluster(launched_nodes)

def dict_request(request):
    """Converts the request into an easy to consume dict format.

    We don't ingest the request in this format originally because dict
    ordering is nondeterministic, and one of our goals is to deterministically
    generate clusters."""
    return dict(
        (dc['name'], dict(dc['racks']))
        for dc in request
    )

def sum_racks(dcs):
    return dict(
        (name, sum(racks.values()))
        for name, racks in dcs.items()
    )

def generate_nodes(request, ips):
    tokens = tokens_for_cluster(request, None)
    counts_by_rack = dict_request(request)
    counts_by_dc = sum_racks(counts_by_rack)
    total_nodes = sum(counts_by_dc.values())
    for dc, racks in tokens:
        dc_count = counts_by_dc[dc]
        rack_count = counts_by_rack[dc]
        remote_count = total_nodes - dc_count
        for rack, tokens in racks:
            local_count = rack_count[rack] - 1
            for token in tokens:
                ip = next(ips)
                yield DynoSpec(ip, INTERNODE_LISTEN, dc, rack, token,
                    local_count, remote_count)

def setup_temp_dir():
    tempdir = LocalPath(mkdtemp(dir='.', prefix='test_run.'))
    (tempdir / 'logs').mkdir()
    confdir = (tempdir / 'conf')
    confdir.mkdir()

    LocalPath('../../conf/dynomite.pem').symlink(confdir / 'dynomite.pem')

    return tempdir

def main():
    parser = argparse.ArgumentParser(
        description='Autogenerates a Dynomite cluster and runs functional ' +
            'tests against it')
    parser.add_argument('request_file', default='test/request.yaml',
        help='YAML file describing desired cluster', nargs='?')
    args = parser.parse_args()

    with open(args.request_file, 'r') as fh:
        request = yaml.load(fh)

    temp = setup_temp_dir()

    ips = generate_ips()
    standalone_redis_ip = next(ips)
    nodes = list(generate_nodes(request, ips))

    with ExitStack() as stack:
        with local.cwd(temp):
            redis_info = stack.enter_context(launch_redis(standalone_redis_ip))
            dynomite_info = stack.enter_context(launch_dynomite(nodes))

            sleep(SETTLE_TIME)
            comparison_test(redis_info, dynomite_info, False)

            random_node = random.choice(dynomite_info.nodes)
            stats_url = 'http://{}:{}/info'.format(random_node.ip, STATS_PORT)
            json.loads(urlopen(stats_url).read().decode('ascii'))

if __name__ == '__main__':
    sys.exit(main())
