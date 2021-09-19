#!/usr/bin/python3

###################################################################################################
from collections import OrderedDict
import json
from ruamel import yaml
import urllib.parse
import urllib.request

###################################################################################################
# Variables
###################################################################################################
github_api = "https://api.github.com/repos/"
docker_api = "https://registry.hub.docker.com/api/content/v1/repositories/public/"
quay_api = "https://quay.io/api/v1/repository/"
file = "latest/base.yml"


###################################################################################################
# Functions
###################################################################################################

def get_schema_is_valid(tag_name, schema):
    if schema == "NUMBER.NUMBER.NUMBER":
        try:
            helper = tag_name.split(".")
        except ValueError:
            return False

        if len(helper) != 3:
            return False

        if helper[0].isdigit() and helper[1].isdigit() and helper[2].isdigit():
            return True

    if schema == "NUMBER.NUMBER":
        try:
            helper = tag_name.split(".")
        except ValueError:
            return False

        if len(helper) != 2:
            return False

        if helper[0].isdigit() and helper[1].isdigit():
            return True

    if schema == "NUMBER.NUMBER-alpine":
        try:
            helper1 = tag_name.split(".")
            helper2 = helper1[1].split("-")
        except IndexError:
            return False
        except ValueError:
            return False

        # NOTE: some versions look like this: 1.19.9-alpine. This filters them away
        if len(helper1) != 2 or len(helper2) != 2:
            return False

        if helper1[0].isdigit() and helper2[0].isdigit() and helper2[1] == "alpine":
            return True

    if schema == "NUMBER-alpine":
        try:
            helper = tag_name.split("-")
        except ValueError:
            return False

        if len(helper) != 2:
            return False

        if helper[0].isdigit() and helper[1] == "alpine":
            return True

    if schema == "vNUMBER.NUMBER":
        if tag_name.startswith("v"):
            try:
                helper1 = tag_name[1:]
                helper2 = helper1.split(".")
            except ValueError:
                return False

            if len(helper2) != 2:
                return False

            if helper2[0].isdigit() and helper2[1].isdigit():
                return True

    return False


def get_api_generic_latest_tag(api, owner, repo, key):
    with urllib.request.urlopen(api + owner + "/" + repo + "/" + key) as url:
        return json.loads(url.read().decode())


def get_api_github_latest_tag(owner, repo, schema):
    result = get_api_generic_latest_tag(github_api, owner, repo, "tags")
    for entry in result:
        if get_schema_is_valid(entry['name'], schema):
            return entry['name']


def get_api_docker_latest_tag(owner, repo, schema):
    result = get_api_generic_latest_tag(docker_api, owner, repo, "tags?page_size=100")
    for entry in result['results']:
        if get_schema_is_valid(entry['name'], schema):
            return entry['name']


def get_api_quay_latest_tag(owner, repo, schema):
    result = get_api_generic_latest_tag(quay_api, owner, repo, "tag/")
    for entry in result['tags']:
        if get_schema_is_valid(entry['name'], schema) and "expiration" not in entry:
            return entry['name']


###################################################################################################

def get_ara_latest_tag():
    return get_api_github_latest_tag("ansible-community", "ara", "NUMBER.NUMBER.NUMBER")


def get_docker_latest_tag():
    result = get_api_docker_latest_tag("library", "docker", "NUMBER.NUMBER.NUMBER")
    return "5:" + result


def get_adminer_latest_tag():
    return get_api_docker_latest_tag("library", "adminer", "NUMBER.NUMBER.NUMBER")


def get_ara_server_latest_tag():
    return get_api_github_latest_tag("ansible-community", "ara", "NUMBER.NUMBER.NUMBER")


def get_awxclient_latest_tag():
    return get_api_quay_latest_tag("ansible", "awx", "NUMBER.NUMBER.NUMBER")


def get_mariadb_latest_tag():
    return get_api_docker_latest_tag("library", "mariadb", "NUMBER.NUMBER")


def get_netbox_latest_tag():
    return get_api_docker_latest_tag("netboxcommunity", "netbox", "vNUMBER.NUMBER")


def get_nexus_latest_tag():
    return get_api_docker_latest_tag("sonatype", "nexus3", "NUMBER.NUMBER.NUMBER")


def get_nginx_latest_tag():
    return get_api_docker_latest_tag("library", "nginx", "NUMBER.NUMBER-alpine")


def get_phpmyadmin_latest_tag():
    return get_api_docker_latest_tag("library", "phpmyadmin", "NUMBER.NUMBER.NUMBER")


def get_postgres_latest_tag():
    # NOTE: postgres releases new 9.X versions along with 10.X, 11.X etc. Therefore direct calling
    current_version = ""

    # NOTE: getting the latest version from Postgres website. This is plain HTML code,
    #       so we need a lot of formatting ...
    with urllib.request.urlopen('https://www.postgresql.org/ftp/') as url:
        webdata = url.read().decode().splitlines()

    for line in webdata:
        if 'latest' in line:
            #   <tr><td><a href="source/v13.3/"><img src="/media/img/ftp/symlink.png" alt="latest -&gt; source/ ...
            current_version = line.split("/")
            # ['  <tr><td><a href="source', 'v13.3', '"><img src="', 'media', 'img', 'ftp', 'symlink.png" alt=" ...
            current_version = current_version[1]
            # 'v13.3'
            current_version = current_version[1:]
            # '13.3'
            # current_version = current_version.split(".")[0]
            # It might be nessecary to strip out beta stuff
            if 'beta' in current_version:
                pass
            else:
                # '13'
                break

    return("%s-alpine" % current_version)


def get_redis_latest_tag():
    return get_api_docker_latest_tag("library", "redis", "NUMBER-alpine")


def get_registry_latest_tag():
    return get_api_docker_latest_tag("library", "registry", "NUMBER.NUMBER")


def set_base(
        latest_ara_version,
        latest_docker_version,
        latest_adminer_verison,
        latest_ara_server_version,
        latest_awxclient_version,
        latest_mariadb_version,
        latest_netbox_version,
        latest_nexus_version,
        latest_nginx_version,
        latest_phpmyadmin_version,
        latest_postgres_version,
        latest_redis_version,
        latest_registry_version):
    print(locals().values())

    # load
    with open(file) as stream:
        try:
            loaded = OrderedDict()
            loaded = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

    # modify
    if loaded['osism_projects']['ara'] is not None:
        loaded['osism_projects']['ara'] = latest_ara_version
    if loaded['osism_projects']['docker'] is not None:
        loaded['osism_projects']['docker'] = latest_docker_version
    if loaded['docker_images']['adminer'] is not None:
        loaded['docker_images']['adminer'] = latest_adminer_verison
    if loaded['docker_images']['ara_server'] is not None:
        loaded['docker_images']['ara_server'] = latest_ara_server_version
    if loaded['docker_images']['awxclient'] is not None:
        loaded['docker_images']['awxclient'] = latest_awxclient_version
    if loaded['docker_images']['mariadb'] is not None:
        loaded['docker_images']['mariadb'] = latest_mariadb_version
    if loaded['docker_images']['netbox'] is not None:
        loaded['docker_images']['netbox'] = latest_netbox_version + "-ldap"
    if loaded['docker_images']['nexus'] is not None:
        loaded['docker_images']['nexus'] = latest_nexus_version
    if loaded['docker_images']['nginx'] is not None:
        loaded['docker_images']['nginx'] = latest_nginx_version
    if loaded['docker_images']['phpmyadmin'] is not None:
        loaded['docker_images']['phpmyadmin'] = latest_phpmyadmin_version
    if loaded['docker_images']['postgres'] is not None:
        loaded['docker_images']['postgres'] = latest_postgres_version
    if loaded['docker_images']['redis'] is not None:
        loaded['docker_images']['redis'] = latest_redis_version
    if loaded['docker_images']['registry'] is not None:
        loaded['docker_images']['registry'] = latest_registry_version

    # replace null with empty strings:
    for i in loaded:
        if isinstance(loaded[i], dict):
            for j in loaded[i]:
                if loaded[i][j] is None:
                    loaded[i][j] = ""

    # save
    with open(file, 'w') as stream:
        try:
            yaml.dump(loaded, stream, default_flow_style=False, explicit_start=True, Dumper=yaml.RoundTripDumper)
        except yaml.YAMLError as exc:
            print(exc)


def restyle_openstack_latest():
    # replace <dummy: ''> with only <dummy:> for better readability

    with open(file, "r") as stream:
        buf = stream.read().replace(" ''", "")
    with open(file, "w") as stream:
        stream.write(buf)

    # insert blank lines for better readability
    with open(file, "r") as stream:
        buf = stream.readlines()
    with open(file, "w") as stream:
        for line in buf:
            if line in ["osism_projects:\n", "docker_images:\n", "ansible_roles:\n"]:
                line = "\n" + line
            stream.write(line)


###################################################################################################
# Main
###################################################################################################
set_base(get_ara_latest_tag(),
         get_docker_latest_tag(),
         get_adminer_latest_tag(),
         get_ara_server_latest_tag(),
         get_awxclient_latest_tag(),
         get_mariadb_latest_tag(),
         get_netbox_latest_tag(),
         get_nexus_latest_tag(),
         get_nginx_latest_tag(),
         get_phpmyadmin_latest_tag(),
         get_postgres_latest_tag(),
         get_redis_latest_tag(),
         get_registry_latest_tag())

restyle_openstack_latest()
