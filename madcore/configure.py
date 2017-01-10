from __future__ import print_function, unicode_literals

import logging
import os
import subprocess
import sys

import boto3
from cliff.lister import Lister

from base import MadcoreBase


class Configure(Lister, MadcoreBase):
    _description = "Config project with all external dependencies"

    log = logging.getLogger(__name__)

    def run_cmd(self, cmd, debug=True, cwd=None):
        if debug:
            self.log.info("Running cmd: %s" % cmd)

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True, cwd=cwd)
        out, err = process.communicate()

        if err:
            self.log.error("ERROR: %s" % err)
        else:
            if debug:
                self.log.info('OK')

        return out.strip()

    def clone_repo(self, repo_url):
        repo_folder = os.path.basename(repo_url).split('.')[0]

        repo_path = os.path.join(self.config_path, repo_folder)

        if not os.path.exists(repo_path):
            self.run_cmd('git clone %s' % repo_url, cwd=self.config_path)
        else:
            self.run_cmd('git pull origin master', cwd=repo_path)

        repo_version = self.run_cmd('git describe --tags', cwd=repo_path, debug=False)

        return repo_version

    def configure_aws(self):
        s = boto3.Session()
        credentials = s.get_credentials()

        if credentials is not None:
            self.log.info("AWS is configured!")
        else:
            aws_cmd = self.run_cmd('which aws', debug=False)
            if not aws_cmd:
                self.log.error("You need to install aws cli!")
                sys.exit(1)
            else:
                self.log.warn("You need to configure aws!")
                os.system('aws configure')

    def take_action(self, parsed_args):
        if not os.path.exists(self.config_path):
            os.makedirs(self.config_path)

        self.configure_aws()

        cf_version = self.clone_repo('https://github.com/madcore-ai/cloudformation.git')
        plugins_version = self.clone_repo('https://github.com/madcore-ai/plugins.git')
        containers_version = self.clone_repo('https://github.com/madcore-ai/containers.git')

        columns = (
            'Project',
            'Version'
        )
        data = (
            ('Cloudformation', cf_version),
            ('Plugins', plugins_version),
            ('Containers', containers_version),
        )

        return columns, data