#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import json
import os
import re
import time
import sys
from multiprocessing.dummy import Pool as ThreadPool
from functools import partial
import protobix
try:
    import requests_unixsocket
except:
    pass

endpoint = 'http+unix://%2Fvar%2Frun%2Fdocker.sock/v1.24/'
docker_tmp_config = '/tmp/docker.conf'


def data_send(data_to_send, pbx_server, pbx_port):
    zbx_datacontainer = protobix.DataContainer()
    zbx_datacontainer.server_active = pbx_server
    zbx_datacontainer.server_port = int(pbx_port)
    zbx_datacontainer.data_type = 'items'
    zbx_datacontainer.add(data_to_send)
    zbx_datacontainer.send()


def get_zabbix_address(mode):
    if mode == 'get':
        with open(docker_tmp_config) as t:
            tmp_config = t.read()
            zbx = tmp_config.split(':')
            return zbx
    if mode == 'check':
        port = '10051'
        with open('/etc/zabbix/zabbix_agentd.conf') as c:
            config_line = c.readlines()
            for line in config_line:
                if "ServerActive" in line:
                    r = re.match(r'^\s*ServerActive\s*=\s*([A-Za-z0-9.-]*):?(\d*)', line)
                    if r:
                        break
        address = r.groups()
        if len(address[1]) > 0:
            port = address[1].strip()
            srv = address[0].strip()
        else:
            srv = address[0].strip()
        file_string = srv + ':' + port
        if not os.path.isfile(docker_tmp_config):
            tmp_config = file_string
            with open(docker_tmp_config, 'w') as t:
                        t.write(tmp_config)
        else:
            with open(docker_tmp_config) as t:
                tmp_config = t.read()
        if tmp_config != file_string:
            tmp_config = file_string
            with open(docker_tmp_config, 'w') as t:
                        t.write(tmp_config)
        zbx = [srv, port]
        return zbx


def list_containers():
    """
    Function gets list of all containers in the system. All parameter in the payload must be true,
    otherwise we will get only running containers
    :return:
    """
    try:
        session = requests_unixsocket.Session()
        payload = {'all': 'true'}
        r = session.get(endpoint + 'containers/json', params=payload)
        containers = json.loads(r.text)
        step_error = None
    except Exception as list_err:
        containers = 'dead'
        step_error = ('list containers : ' + str(list_err))
    return containers, step_error


def dockerd_info():
    try:
        session = requests_unixsocket.Session()
        r = session.get(endpoint + 'info')
        dockerd = json.loads(r.text)
        step_error = None
    except Exception as dock_err:
        dockerd = 'dead'
        step_error = ('dockerd_info: ' + str(dock_err))
    return dockerd, step_error


def inspect(name):
    try:
        session = requests_unixsocket.Session()
        r = session.get(endpoint + 'containers/' + name + '/json')
        inspected = json.loads(r.text)
        step_error = None
    except Exception as ins_err:
        step_error = ('inspect: ' + str(ins_err))
        inspected = 'dead'
    return inspected, step_error


def stats(name):
    try:
        stat = 'containers/' + name + '/stats'
        session = requests_unixsocket.Session()
        payload = {'stream': 'false'}
        r = session.get(endpoint + stat, params=payload)
        response = json.loads(r.text)
        step_error = None
    except Exception as st_err:
        step_error = ('Get stats: ' + str(st_err))
        response = 'dead'
    return response, step_error


def discover(list_res, host_name, pbx_srv, pbx_port):
    if list_res[1] is None:
        zbx_data = []
        for i in list_res[0]:
            zbx_data.append({"{#DOCKER.CONTAINER.NAME}": i['Names'][0][1:], "{#DOCKER.CONTAINER.ID}": i['Id']})
    else:
        zbx_data = []
        pbx_data = {
            host_name: {
                "script_error": list_res[1]
            }
        }
        data_send(pbx_data, pbx_srv, pbx_port)
    return zbx_data


def get_cpu(stats_res, inspect_res):
    """
    Calculating CPU usage of the container
    :param stats_res:
    :return:
    """
    inspect_res = inspect_res[0]
    stats_res = stats_res[0]
    cpu_rate = inspect_res["HostConfig"]["CpuQuota"]/1000
    if cpu_rate == 0:
        cpu_rate = 100
    cpu_percent = 0.0
    cpu_delta = float(stats_res["cpu_stats"]["cpu_usage"]["total_usage"]) - float(
        stats_res["precpu_stats"]["cpu_usage"]["total_usage"])
    system_delta = float(stats_res["cpu_stats"]["system_cpu_usage"]) - float(
        stats_res["precpu_stats"]["system_cpu_usage"])
    if system_delta > 0.0:
        cpu_percent = cpu_delta / system_delta * cpu_rate
    if cpu_percent > 100:
        cpu_percent = 100
    return cpu_percent


def get_mem(stats_res):
    stats_res = stats_res[0]
    try:
        mem_usage = (int(stats_res['memory_stats']['usage']) - int(stats_res['memory_stats']['stats']['cache']))
        mem_limit = int(stats_res['memory_stats']['limit'])
        mem_percent = mem_usage/mem_limit*100
    except:
        return 0
    return mem_usage, mem_percent


def get_state(inspect_res):
    inspect_res = inspect_res[0]
    state = inspect_res['State']['Status']
    return state


def get_exit_code(inspect_res):
    inspect_res = inspect_res[0]
    code = inspect_res['State']['ExitCode']
    return code


def get_count_overall(dockerd_res):
    dockerd_res = dockerd_res[0]
    count = dockerd_res['Containers']
    return count


def get_count_running(dockerd_res):
    dockerd_res = dockerd_res[0]
    count = dockerd_res['ContainersRunning']
    return count


def get_count_paused(dockerd_res):
    dockerd_res = dockerd_res[0]
    count = dockerd_res['ContainersPaused']
    return count


def get_count_stopped(dockerd_res):
    dockerd_res = dockerd_res[0]
    count = dockerd_res['ContainersStopped']
    return count


def host_data(info_res, host_name, pbx_srv, pbx_port):
    if info_res[1] is None:
        dockerd_alive = 0
        count_all = get_count_overall(info_res)
        count_run = get_count_running(info_res)
        count_stopped = get_count_stopped(info_res)
        count_paused = get_count_paused(info_res)
        host_send_data = {
            host_name: {
                "DOCKER.HOST.CONTAINER.ALL": count_all,
                "DOCKER.HOST.CONTAINER.RUN": count_run,
                "DOCKER.HOST.CONTAINER.STOP": count_stopped,
                "DOCKER.HOST.CONTAINER.PAUSE": count_paused
            }
        }
        data_send(host_send_data, pbx_srv, pbx_port)
    else:
        dockerd_alive = 1
        host_error = info_res[1]
        host_send_data = {
            host_name: {
                "script_error": host_error
            }
        }
        data_send(host_send_data, pbx_srv, pbx_port)
    return dockerd_alive


def obtain_info(cont, host_name, pbx_srv, pbx_port):
    state = cont['State']
    states = {
        'unknown': 0,
        'created': 1,
        'restarting': 2,
        'removing': 3,
        'paused': 4,
        'exited': 5,
        'dead': 6,
        'running': 10
    }
    status = states.get(state, 0)
    cont_name = cont['Names'][0][1:]
    inspect_res = inspect(cont_name)
    if status == 10:
        stats_res = stats(cont_name)
        cpu = get_cpu(stats_res, inspect_res)
        mem = get_mem(stats_res)
        if mem:
            container_data = {
                host_name: {
                    "status[" + cont_name + "]": int(status),
                    "cpu[" + cont_name + "]": int(cpu),
                    "mem[" + cont_name + "]": int(mem[0]),
                    "mempercent[" + cont_name + "]": int(mem[1])
                }
            }
        else:
            container_data = {
                host_name: {
                    "status[" + cont_name + "]": int(status),
                    "cpu[" + cont_name + "]": int(cpu)
                }
            }
    else:
        e_code = get_exit_code(inspect_res)
        container_data = {
            host_name: {
                "status[" + cont_name + "]": status,
                "e_code[" + cont_name + "]": e_code
            }
        }
    data_send(container_data, pbx_srv, pbx_port)


def containers_data(host_name, pbx_srv, pbx_port):
    conts = list_containers()
    if conts[1] is None:
        pool = ThreadPool(10)
        obtain=partial(obtain_info, host_name=host_name, pbx_srv=pbx_srv, pbx_port=pbx_port)
        conts = conts[0]
        pool.map(obtain, conts)
    else:
        container_error = conts[1]
        container_data = {
            host_name: {
                "script_error": container_error
            }
        }
        data_send(container_data, pbx_srv, pbx_port)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', help='script action. "discover" for LLD, "check" for all other checks',
                        required=True)
    parser.add_argument('--host', help='hostname from zabbix', required=True)
    args = parser.parse_args()

    hostname = args.host

    if not os.path.isfile('/usr/bin/docker'):
        if args.action == 'check':
            print(0)
            return 0
        elif args.action == 'discover':
            print(json.dumps({"data": []}))
            return 0

    if args.action == 'check':
        if os.path.isfile(docker_tmp_config):
            zabbix_addr = get_zabbix_address('get')
        else:
            zabbix_addr = get_zabbix_address('check')
        zbx_srv = zabbix_addr[0]
        zbx_port = int(zabbix_addr[1])
        if zbx_srv is not None:
            try:
                dockerinfo = dockerd_info()
                if dockerinfo[1] is None:
                    print(0)
                    hostdata = host_data(dockerinfo, hostname, zbx_srv, zbx_port)
                    if not hostdata:
                        containers_data(hostname, zbx_srv, zbx_port)
                    else:
                        error = hostdata[1]
                        protobix_data = {
                            hostname: {
                                "script_error": error
                            }
                        }
                        data_send(protobix_data, zbx_srv, zbx_port)
                else:
                    print(1)
                    error = dockerinfo[1]
                    protobix_data = {
                        hostname: {
                            "script_error": error
                        }
                    }
                    data_send(protobix_data, zbx_srv, zbx_port)

            except Exception as err:
                error = str(err)
                protobix_data = {
                    hostname: {
                        "script_error": error
                    }
                }

                data_send(protobix_data, zbx_srv, zbx_port)
        else:
            print('Zabbix server address is not specified')

    elif args.action == 'discover':
        zabbix_addr = get_zabbix_address('check')
        zbx_srv = zabbix_addr[0]
        zbx_port = int(zabbix_addr[1])
        data = discover(list_containers(), hostname, zbx_srv, zbx_port)
        print(json.dumps({"data": data}))

    else:
        print('invalid params')

if __name__ == "__main__":
    main()
