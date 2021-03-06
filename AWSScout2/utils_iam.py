#!/usr/bin/env python2

# Import AWS Utils
from AWSUtils.utils_iam import *

# Import Scout2 tools
from AWSScout2.utils import *
from AWSScout2.filters import *
from AWSScout2.findings import *

# Import third-party packages
import base64
try:
    import boto3 # h4ck # Because boto doesn't support managed policies yet...
except:
    print 'You need to install boto3 if you want Scout2 to fetch the managed policies.'
    pass
import json
import urllib


########################################
##### IAM functions
########################################

def analyze_iam_config(iam_info, force_write):
    sys.stdout.write('Analyzing IAM data...\n')
    analyze_config(iam_finding_dictionary, iam_filter_dictionary, iam_info, 'IAM', force_write)

def get_groups_info(iam_connection, iam_info):
    groups = handle_truncated_responses(iam_connection.get_all_groups, None, ['list_groups_response', 'list_groups_result'], 'groups')
    iam_info['groups_count'] = len(groups)
    thread_work(iam_connection, iam_info, groups, get_group_info, num_threads = 10)
    show_status(iam_info, 'groups')

def get_group_info(iam_connection, q, params):
    while True:
        try:
            iam_info, group = q.get()
            # When resuming upon throttling error, skip if already fetched
            if group['group_name'] in iam_info['groups']:
                continue
            group['id'] = group.pop('group_id')
            group['name'] = group.pop('group_name')
            group['users'] = get_group_users(iam_connection, group.name);
            policies = get_policies(iam_connection, iam_info, 'group', group.name)
            if len(policies):
                group['policies'] = policies
            iam_info['groups'][group.name] = group
            show_status(iam_info, 'groups', False)
        except Exception, e:
            printException(e)
            pass
        finally:
            q.task_done()

def get_group_users(iam, group_name):
    users = []
    fetched_users = iam.get_group(group_name).get_group_response.get_group_result.users
    for user in fetched_users:
        users.append(user.user_name)
    return users

def get_iam_info(key_id, secret, session_token, iam_info):
    manage_dictionary(iam_info, 'groups', {})
    manage_dictionary(iam_info, 'permissions', {})
    manage_dictionary(iam_info, 'roles', {})
    manage_dictionary(iam_info, 'users', {})
    iam_connection = connect_iam(key_id, secret, session_token)
    # Generate the report early so that download doesn't fail with "ReportInProgress".
    try:
        iam_connection.generate_credential_report()
    except Exception, e:
        pass
    print 'Fetching IAM users...'
    get_users_info(iam_connection, iam_info)
    print 'Fetching IAM groups...'
    get_groups_info(iam_connection, iam_info)
    print 'Fetching IAM roles...'
    get_roles_info(iam_connection, iam_info)
    try:
        print 'Fetching IAM policies...'
        get_managed_policies(key_id, secret, session_token, iam_info)
    except Exception, e:
        printException(e)
        pass
    print 'Fetching IAM credential report...'
    get_credential_report(iam_connection, iam_info)

def get_permissions(policy_document, permissions, keyword, name, policy_name, is_managed_policy = False):
    manage_dictionary(permissions, 'Action', {})
    manage_dictionary(permissions, 'NotAction', {})
    document = json.loads(urllib.unquote(policy_document).decode('utf-8'))
    if type(document['Statement']) != list:
        parse_statement(policy_document, permissions, keyword, name, policy_name, is_managed_policy, document['Statement'])
    else:
        for statement in document['Statement']:
            parse_statement(policy_document, permissions, keyword, name, policy_name, is_managed_policy, statement)

def parse_statement(policy_document, permissions, keyword, name, policy_name, is_managed_policy, statement):
        effect = str(statement['Effect'])
        action_string = 'Action' if 'Action' in statement else 'NotAction'
        resource_string = 'Resource' if 'Resource' in statement else 'NotResource'
        condition = statement['Condition'] if 'Condition' in statement else None
        parse_actions(permissions[action_string], statement[action_string], resource_string, statement[resource_string], effect, keyword, name, policy_name, is_managed_policy, condition)

def parse_actions(permissions, actions, resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition):
    if type(actions) == list:
        for action in actions:
            parse_action(permissions, action, resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition)
    else:
        parse_action(permissions, actions, resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition)

def parse_action(permissions, action, resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition):
    manage_dictionary(permissions, action, {})
    parse_resources(permissions[action], resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition)

def parse_resources(permission, resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition):
    if type(resources) == list:
        for resource in resources:
            parse_resource(permission, resource_string, resource, effect, keyword, name, policy_name, is_managed_policy, condition)
    else:
        parse_resource(permission, resource_string, resources, effect, keyword, name, policy_name, is_managed_policy, condition)

def parse_resource(permission, resource_string, resource, effect, keyword, name, policy_name, is_managed_policy, condition):
    manage_dictionary(permission, keyword, {})
    manage_dictionary(permission[keyword], effect, {})
    manage_dictionary(permission[keyword][effect], name, {})
    manage_dictionary(permission[keyword][effect][name], resource_string, {})
    manage_dictionary(permission[keyword][effect][name][resource_string], resource, {})
    if is_managed_policy:
        policy_type = 'ManagedPolicies'
    else:
        policy_type = 'InlinePolicies'
    manage_dictionary(permission[keyword][effect][name][resource_string][resource], policy_type, {})
    manage_dictionary(permission[keyword][effect][name][resource_string][resource][policy_type], policy_name, {})
    permission[keyword][effect][name][resource_string][resource][policy_type][policy_name]['condition'] = condition

def handle_truncated_boto3(iam_method, params, entities):
    results = {}
    for entity in entities:
        results[entity] = []
    while True:
        response = iam_method(**params)
        for entity in entities:
            results[entity] = results[entity] + response[entity]
        if 'IsTruncated' in response and response['IsTruncated'] == True:
            params['Marker'] = response['Marker']
        else:
            break
    return results

def get_managed_policies(key_id, secret, token, iam_info):
    boto3_session = boto3.session.Session(aws_access_key_id = key_id, aws_secret_access_key = secret, aws_session_token = token)
    iam_connection3 = boto3_session.resource('iam')
    policies = []
    params = {}
    params['OnlyAttached'] = True
    policies = handle_truncated_boto3(iam_connection3.meta.client.list_policies, params, ['Policies'])
    manage_dictionary(iam_info, 'managed_policies', {})
    iam_info['managed_policies_count'] = len(policies['Policies'])
    show_status(iam_info, 'managed_policies', False)
    thread_work(iam_connection3, iam_info, policies['Policies'], get_managed_policy, num_threads = 10)
    show_status(iam_info, 'managed_policies')

def get_managed_policy(iam_connection3, q, params):
    while True:
        try:
            iam_info, policy = q.get()
            manage_dictionary(iam_info['managed_policies'], policy['Arn'], {})
            iam_info['managed_policies'][policy['Arn']]['policy_name'] = policy['PolicyName']
            iam_info['managed_policies'][policy['Arn']]['policy_id'] = policy['PolicyId']
            # Download version and document
            policy_version = iam_connection3.meta.client.get_policy_version(PolicyArn = policy['Arn'], VersionId = policy['DefaultVersionId'])
            policy_version = policy_version['PolicyVersion']
            policy_document = urllib.quote(json.dumps(policy_version['Document']))
            iam_info['managed_policies'][policy['Arn']]['policy_document'] = policy_document
            # Get attached IAM entities
            attached_entities = handle_truncated_boto3(iam_connection3.meta.client.list_entities_for_policy, {'PolicyArn': policy['Arn']}, ['PolicyGroups', 'PolicyRoles', 'PolicyUsers'])
            for entity_type in attached_entities:
                type_field = entity_type.replace('Policy', '').lower()
                for entity in attached_entities[entity_type]:
                    name_field = entity_type.replace('Policy', '')[:-1] + 'Name'
                    manage_dictionary(iam_info[type_field][entity[name_field]], 'managed_policies', [])
                    iam_info[type_field][entity[name_field]]['managed_policies'].append(policy['Arn'])
                    get_permissions(policy_document, iam_info['permissions'], type_field, entity[name_field], policy['Arn'], True)
            show_status(iam_info, 'managed_policies', False)
        except Exception, e:
            printException(e)
            pass
        finally:
            q.task_done()

def get_policies(iam_connection, iam_info, keyword, name):
    fetched_policies = {}
    if keyword == 'role':
        m1 = getattr(iam_connection, 'list_role_policies', None)
    else:
        m1 = getattr(iam_connection, 'get_all_' + keyword + '_policies', None)
    if m1:
        policy_names = m1(name)
    else:
        print 'Unknown error'
    policy_names = policy_names['list_' + keyword + '_policies_response']['list_' + keyword + '_policies_result']['policy_names']
    get_policy_method = getattr(iam_connection, 'get_' + keyword + '_policy')
    for policy_name in policy_names:
        r = get_policy_method(name, policy_name)
        r = r['get_'+keyword+'_policy_response']['get_'+keyword+'_policy_result']
        manage_dictionary(fetched_policies, policy_name, {})
        fetched_policies[policy_name]['policy_document'] = r.policy_document
        get_permissions(r.policy_document, iam_info['permissions'], keyword + 's', name, policy_name)
    return fetched_policies

def get_roles_info(iam_connection, iam_info):
    roles = handle_truncated_responses(iam_connection.list_roles, None, ['list_roles_response', 'list_roles_result'], 'roles')
    iam_info['roles_count'] = len(roles)
    thread_work(iam_connection, iam_info, roles, get_role_info, num_threads = 10)
    show_status(iam_info, 'roles')

def get_role_info(iam_connection, q, params):
    while True:
        try:
            iam_info, role = q.get()
            # When resuming upon throttling error, skip if already fetched
            if role['role_name'] in iam_info['roles']:
                continue
            role['id'] = role.pop('role_id')
            role['name'] = role.pop('role_name')
            policies = get_policies(iam_connection, iam_info, 'role', role.name)
            if len(policies):
                role['policies'] = policies
            iam_info['roles'][role.name] = role
            profiles = handle_truncated_responses(iam_connection.list_instance_profiles_for_role, role.name, ['list_instance_profiles_for_role_response', 'list_instance_profiles_for_role_result'], 'instance_profiles')
            manage_dictionary(role, 'instance_profiles', {})
            for profile in profiles:
                manage_dictionary(role['instance_profiles'], profile['arn'], {})
                role['instance_profiles'][profile['arn']]['id'] = profile['instance_profile_id']
                role['instance_profiles'][profile['arn']]['name'] = profile['instance_profile_name']
            show_status(iam_info, 'roles', False)
        except Exception, e:
            printException(e)
            pass
        finally:
            q.task_done()

def get_credential_report(iam_connection, iam_info):
    iam_report = {}
    try:
        report = iam_connection.get_credential_report()
        report = base64.b64decode(report['get_credential_report_response']['get_credential_report_result']['content'])
        lines = report.split('\n')
        keys = lines[0].split(',')
        for line in lines[1:]:
            values = line.split(',')
            manage_dictionary(iam_report, values[0], {})
            for key, value in zip(keys, values):
                iam_report[values[0]][key] = value
        iam_info['credential_report'] = iam_report
    except Exception, e:
        print 'Failed to generate/download a credential report.'
        print e

def get_users_info(iam_connection, iam_info):
    users = handle_truncated_responses(iam_connection.get_all_users, None, ['list_users_response', 'list_users_result'], 'users')
    iam_info['users_count'] = len(users)
    thread_work(iam_connection, iam_info, users, get_user_info, num_threads = 10)
    show_status(iam_info, 'users')

def get_user_info(iam_connection, q, params):
    while True:
        try:
            iam_info, user = q.get()
            # When resuming upon throttling error, skip if already fetched
            if user['user_name'] in iam_info['users']:
                continue
            user['id'] = user.pop('user_id')
            user['name'] = user.pop('user_name')
            policies = get_policies(iam_connection, iam_info, 'user', user.name)
            if len(policies):
                user['policies'] = policies
            groups = iam_connection.get_groups_for_user(user['name'])
            user['groups'] = groups.list_groups_for_user_response.list_groups_for_user_result.groups
            try:
                logins = iam_connection.get_login_profiles(user['name'])
                user['logins'] = logins.get_login_profile_response.get_login_profile_result.login_profile
            except Exception, e:
                pass
            access_keys = iam_connection.get_all_access_keys(user['name'])
            user['access_keys'] = access_keys.list_access_keys_response.list_access_keys_result.access_key_metadata
            mfa_devices = iam_connection.get_all_mfa_devices(user['name'])
            user['mfa_devices'] = mfa_devices.list_mfa_devices_response.list_mfa_devices_result.mfa_devices
            iam_info['users'][user['name']] = user
            show_status(iam_info, 'users', False)
        except Exception, e:
            printException(e)
            pass
        finally:
            q.task_done()

def show_status(iam_info, entities, newline = True):
    current = len(iam_info[entities])
    total = iam_info[entities + '_count']
    sys.stdout.write("\r%d/%d" % (current, total))
    sys.stdout.flush()
    if newline:
        sys.stdout.write('\n')
