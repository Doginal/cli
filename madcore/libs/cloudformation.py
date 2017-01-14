from __future__ import print_function, unicode_literals

import logging

from cliff.formatters.table import TableFormatter

from madcore import const
from madcore import utils
from madcore.base import CloudFormationBase
from madcore.base import Stdout
from madcore.configs import config
from madcore.libs.aws import AwsLambda


class StackManagement(CloudFormationBase):
    log = logging.getLogger(__name__)

    def __init__(self, app, *args, **kwargs):
        super(StackManagement, self).__init__(app, *args, **kwargs)
        self.app = app
        self.formatter = TableFormatter()

    def produce_output(self, parsed_args, column_names, data):
        self.formatter.emit_list(column_names, data, Stdout(), parsed_args)

    def stack_show_output_parameters(self, stack_details, parsed_args):
        def show_output(results_key, column_names):
            data = []

            if results_key in stack_details:
                for param in stack_details[results_key]:
                    data.append([param.get(c, '') for c in column_names])

                self.produce_output(parsed_args, column_names, data)

        self.log.info("[{StackName}] Output parameters for stack:".format(**stack_details))
        show_output('Outputs', ['OutputKey', 'OutputValue', 'Description'])

    def stack_show_input_parameter(self, stack_short_name, input_params, parsed_args, debug=True):
        stack_name = self.stack_name(stack_short_name)

        def show_output(column_names):
            data = []

            for param in input_params:
                data.append([param[c] for c in column_names])

            self.produce_output(parsed_args, column_names, data)

        if input_params != [{}]:
            if debug:
                self.log.info("[%s] Input parameters for stack:", stack_name)
            show_output(['ParameterKey', 'ParameterValue'])
        else:
            if debug:
                self.log.info("[%s] No input parameters for stack.", stack_name)

    @classmethod
    def is_stack_create_failed(cls, stack_details):
        if stack_details['StackStatus'] in ['ROLLBACK_COMPLETE']:
            return True

        return False

    def delete_stack(self, stack_short_name, show_progress=True):
        stack_name = self.stack_name(stack_short_name)

        response = self.cf_client.delete_stack(
            StackName=stack_name
        )

        if show_progress:
            self.show_stack_delete_events_progress(stack_name)

        return response

    def delete_stack_if_exists(self, stack_short_name):
        stack_deleted = False
        stack_name = self.stack_name(stack_short_name)

        stack_details = self.get_stack(stack_name, debug=False)

        if stack_details is not None:
            self.log.info("[%s] Stack exists, delete...", stack_name)
            self.delete_stack(stack_short_name)
            self.log.info("[%s] Stack deleted.", stack_name)
            stack_deleted = True
        else:
            self.log.info("[%s] Stack does not exists, skip.", stack_name)

        return stack_deleted

    def create_stack(self, stack_short_name, input_parameters, capabilities=None, show_progress=True):
        stack_name = self.stack_name(stack_short_name)
        template_file = '%s.json' % stack_short_name.lower()

        if not input_parameters:
            input_parameters = [{}]

        response = self.cf_client.create_stack(
            StackName=stack_name,
            TemplateBody=self.get_template_local(template_file),
            Parameters=input_parameters,
            Capabilities=capabilities or []
        )

        if show_progress:
            self.show_stack_create_events_progress(stack_name)

        return response

    def create_stack_if_not_exists(self, stack_short_name, dict_params, parsed_args, capabilities=None):
        exists = False
        error = False
        updated = False

        # construct the parameters for the stack from the dict
        stack_params = self.create_stack_parameters(dict_params=dict_params)

        stack_name = self.stack_name(stack_short_name)

        self.stack_show_input_parameter(stack_short_name, stack_params, parsed_args)

        stack_details = self.get_stack(stack_name, debug=False)

        if stack_details is None:
            self.log.info("[%s] Stack does not exists, creating it...", stack_name)
            self.create_stack(stack_short_name, stack_params, capabilities=capabilities)
            stack_details = self.get_stack(stack_name)
            if stack_details and not self.is_stack_create_failed(stack_details):
                self.log.info("[{StackName}] Stack created with status '{StackStatus}'.\n".format(**stack_details))
            else:
                self.log.error("[%s] Error while creating stack. Check logs for details.", stack_name)
                error = True
        elif self.is_stack_create_failed(stack_details):
            self.log.info(
                "[{StackName}] Stack is created but failed with status '{StackStatus}'".format(**stack_details))
            self.log.info("[%s] Try to create again.", stack_name)
            self.delete_stack(stack_short_name)
            self.create_stack(stack_short_name, stack_params, capabilities=capabilities)
            stack_details = self.get_stack(stack_name)
            if stack_details and not self.is_stack_create_failed(stack_details):
                self.log.info("[{StackName}] Stack recreated with status '{StackStatus}'.\n".format(**stack_details))
            else:
                self.log.error("[%s] Error while creating stack. Check logs for details.", stack_name)
                error = True
        else:
            self.log.info("[%s] Stack already exists, skip.", stack_name)
            updated = self.update_stack_if_changed(stack_short_name, stack_details, dict_params, parsed_args,
                                                   capabilities)
            stack_details = self.get_stack(stack_name, debug=False)
            exists = True

        if not error:
            self.stack_show_output_parameters(stack_details, parsed_args)
        else:
            self.exit()

        return stack_details, exists, updated

    def update_stack(self, stack_short_name, input_parameters, capabilities=None, show_progress=True):
        stack_name = self.stack_name(stack_short_name)
        template_file = '%s.json' % stack_short_name.lower()

        if not input_parameters:
            input_parameters = [{}]

        response = self.cf_client.update_stack(
            StackName=stack_name,
            TemplateBody=self.get_template_local(template_file),
            Parameters=input_parameters,
            Capabilities=capabilities or []
        )

        if show_progress:
            self.show_stack_update_events_progress(stack_name)

        return response

    def update_stack_if_changed(self, stack_short_name, stack_details, stack_create_parameters, parsed_args,
                                capabilities=None, show_progress=True):
        updated = False
        stack_name = self.stack_name(stack_short_name)

        self.log.info("[%s] Try to update stack if needed.", stack_name)

        stack_update_params = []
        updated_params = []

        # check if there are diff between stack params and new stack params
        for stack_param in stack_details.get('Parameters', []):
            if stack_param['ParameterKey'] in stack_create_parameters:
                param = {
                    'ParameterKey': stack_param['ParameterKey'],
                }
                if stack_param['ParameterValue'] != stack_create_parameters[stack_param['ParameterKey']]:
                    param['ParameterValue'] = stack_create_parameters[stack_param['ParameterKey']]
                    updated_params.append(param)
                    param['UsePreviousValue'] = False
                else:
                    # we don't need ParameterValue here when  we specify UsePreviousValue
                    param['UsePreviousValue'] = True

                stack_update_params.append(param)

        if updated_params:
            self.log.info("[%s] Stack params changed, show params that require update.", stack_name)
            self.stack_show_input_parameter(stack_short_name, updated_params, parsed_args, debug=False)
            self.log.info("[%s] Start updating stack.", stack_name)
            self.update_stack(stack_short_name, stack_update_params, capabilities, show_progress)
            updated = True
        else:
            self.log.info("[%s] There are no params to update, skip.", stack_name)

        return updated

    @classmethod
    def create_stack_parameters(cls, dict_params={}):
        cf_params = []

        if not dict_params:
            cf_params.append({})
        else:
            for param_key, param_value in dict_params.iteritems():
                cf_param = {
                    'ParameterKey': param_key,
                    'ParameterValue': param_value
                }
                cf_params.append(cf_param)

        return cf_params

    def start_instance_if_not_running(self, instance_id):
        self.log.info("Check if madcore instance is running.")
        try:
            instance_details = self.describe_instance(instance_id)

            instance_status = instance_details['State']['Name']
            if instance_status in ['stopped', 'stopping']:
                self.log.info("Madcore instance is not running, current status is: '%s'.", instance_status)
                self.log.info("Start madcore instance...")
                ec2_cli = self.get_aws_client('ec2')
                ec2_cli.start_instances(
                    InstanceIds=[instance_id]
                )
                # wait until instance is running
                ec2_cli.get_waiter('instance_running').wait(
                    InstanceIds=[instance_id]
                )
                self.log.info("Madcore instance is running.")
                return True
            else:
                self.log.info("Madcore instance is already running.")
        except Exception as e:
            self.log.error("Error while starting instance '%s'.", instance_id)
            self.log.error(e)

        return False


class StackCreate(StackManagement):
    def take_action(self, parsed_args):
        # create S3
        self.log_piglet("STACK %s", const.STACK_S3)
        s3_stack, s3_exists, _ = self.create_stack_if_not_exists('s3', {}, parsed_args)

        self.log_piglet("STACK %s", const.STACK_NETWORK)
        # create Network
        network_parameters = {
            'Type': 'Production',
            'Env': 'madcore',
            'VPCCIDRBlock': '10.99.0.0/16'
        }
        network_stack, network_exists, _ = self.create_stack_if_not_exists('network', network_parameters, parsed_args)

        self.log_piglet("STACK %s", const.STACK_FOLLOWME)
        # create SGFM
        sgfm_parameters = {
            'FollowMeIpAddress': self.get_ipv4(),
            'VpcId': self.get_output_from_dict(network_stack['Outputs'], 'VpcId')
        }
        sgfm_stack, sgfm_exists, _ = self.create_stack_if_not_exists('sgfm', sgfm_parameters, parsed_args)

        self.log_piglet("STACK %s", const.STACK_CORE)
        # create Core
        aws_config = config.get_aws_data()
        core_parameters = {
            'FollowmeSecurityGroup': self.get_output_from_dict(sgfm_stack['Outputs'], 'FollowmeSgId'),
            'PublicNetZoneA': self.get_output_from_dict(network_stack['Outputs'], 'PublicNetZoneA'),
            'S3BucketName': self.get_output_from_dict(s3_stack['Outputs'], 'S3BucketName'),
            'InstanceType': aws_config['instance_type'],
            'KeyName': aws_config['key_name']
        }
        core_capabilities = ["CAPABILITY_IAM"]

        core_stack = self.get_stack_by_short_name('core', debug=False)

        if core_stack is not None and not self.is_stack_create_failed(core_stack):
            core_instance_id = self.get_output_from_dict(core_stack['Outputs'], 'MadCoreInstanceId')
            self.log.debug("[%s] Check if madcore instance is terminated...", const.STACK_CORE)
            if self.is_instance_terminated(core_instance_id):
                self.log.info("[%s] Madcore instance is terminated, recreate stack.", const.STACK_CORE)
                self.log.info("[%s] Delete stack.", core_stack['StackName'])
                self.delete_stack('core')
            else:
                self.log.debug("[%s] Instance not terminated.", const.STACK_CORE)

            self.start_instance_if_not_running(core_instance_id)

        core_stack, core_exists, _ = self.create_stack_if_not_exists('core', core_parameters, parsed_args,
                                                                     capabilities=core_capabilities)

        # TODO@geo get here the KeyPair, InstanceType to be set into config if user is registered
        # and not yet sync locally but stack is created and running

        self.log_piglet("STACK %s", const.STACK_DNS)
        # create DNS
        user_config = config.get_user_data()
        dns_parameters = {
            'DomainName': user_config['domain'],
            'SubDomainName': user_config['sub_domain'],
            'EC2PublicIP': self.get_output_from_dict(core_stack['Outputs'], 'MadCorePublicIp'),
        }
        dns_stack, dns_exists, dns_updated = self.create_stack_if_not_exists('dns', dns_parameters, parsed_args)

        self.log_piglet("DNS delegation")
        # do DNS delegation
        if not config.is_dns_delegated or dns_updated:
            self.log.info("DNS delegation start")
            aws_lambda = AwsLambda()
            name_servers = self.get_hosted_zone_name_servers(
                self.get_output_from_dict(dns_stack['Outputs'], 'HostedZoneID'))

            delegation_response = aws_lambda.dns_delegation(name_servers)

            if delegation_response.get('verified', False):
                self.log.info("DNS delegation verified.")
                dns_delegation = True
            else:
                self.log.error("DNS delegation error: %s", delegation_response)
                dns_delegation = False

            config.set_user_data({'dns_delegation': dns_delegation})
            self.log.info("DNS delegation end.")

            if not dns_delegation:
                self.exit()

            self.log.info("Wait until DNS for domain '%s' is resolved...", config.get_full_domain())
            if utils.hostname_resolves(config.get_full_domain()):
                self.log.info("DNS resolved.")
            else:
                self.log.error("DNS not resolvable.")
        else:
            self.log.info("DNS delegation already setup.")

        self.log.info("Stack Create status:")
        self.produce_output(parsed_args,
                            ('StackName', 'Created'),
                            (
                                (const.STACK_S3, not s3_exists),
                                (const.STACK_NETWORK, not network_exists),
                                (const.STACK_FOLLOWME, not sgfm_exists),
                                (const.STACK_CORE, not core_exists),
                                (const.STACK_DNS, not dns_exists)
                                # (const.STACK_CLUSTER, not cluster_exists)
                            )
                            )
