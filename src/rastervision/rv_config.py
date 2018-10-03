import os
import json
import tempfile
import shutil
import atexit

from pathlib import Path

from everett.manager import (ConfigManager, ConfigDictEnv, ConfigEnvFileEnv,
                             ConfigIniEnv, ConfigOSEnv)

import rastervision as rv
from rastervision.utils.files import file_to_str
from rastervision.filesystem.local_filesystem import make_dir


class RVConfig:
    DEFAULT_PROFILE = 'default'

    # We need to use a custom location for the temporary directory so
    # it will be mirrored on the host file system which is needed for
    # running in a Docker container with limited space on EC2.
    DEFAULT_DIR = '/opt/data/tmp/'

    tmp_dir = None

    @staticmethod
    def get_tmp_dir():
        tempfile.tempdir = RVConfig.tmp_dir
        return tempfile.TemporaryDirectory()

    @staticmethod
    def set_tmp_dir(tmp_dir=None, rm_tmp_dir=False):
        """Set RVConfig.tmp_dir to well-known value.

        This static method sets the value of RVConfig.tmp_dir to some
        well-known value.  The value is chosen from one of the
        following (in order of preference): an explicit value
        (presumably from the command line) is considered first, then
        values from the environment are considered, then the current
        value of RVConfig.tmp_dir is considered, then the default
        value (given by RVConfig.DEFAULT_DIR) is considered.

        Args:
            tmp_dir: Either a string or None.

        """
        # Check the various possibilities in order of priority.
        tmp_dir_array = [tmp_dir]
        env_array = [
            os.environ.get(k) for k in ['TMPDIR', 'TEMP', 'TMP']
            if k in os.environ
        ]
        current_array = [RVConfig.tmp_dir]
        it = iter(tmp_dir_array + env_array + current_array)
        explicit_tmp_dir = next(filter(lambda p: p is not None, it))

        try:
            # Try to create directory
            if not os.path.exists(explicit_tmp_dir):
                os.makedirs(explicit_tmp_dir, exist_ok=True)
            # Check that it is actually a directory
            if not os.path.isdir(explicit_tmp_dir):
                raise Exception(
                    '{} is not a directory.'.format(explicit_tmp_dir))
            # Can we interact with directory?
            Path.touch(Path(os.path.join(explicit_tmp_dir, '.can_touch')))
            # All checks have passed by this point
            RVConfig.tmp_dir = explicit_tmp_dir

        # If directory cannot be made and/or cannot be interacted
        # with, fall back to default.
        except Exception:
            print(
                'Root temporary directory cannot be used: {}. Using root: {}'.
                format(explicit_tmp_dir, RVConfig.DEFAULT_DIR))
            RVConfig.tmp_dir = RVConfig.DEFAULT_DIR

        finally:
            make_dir(RVConfig.tmp_dir)
            tmp_dir = RVConfig.tmp_dir
            if rm_tmp_dir:
                atexit.register(lambda: shutil.rmtree(tmp_dir))
                print('{} directory will be removed upon nominal exit.'.format(
                    tmp_dir))
            print('Temporary directory is: {}'.format(RVConfig.tmp_dir))

    @staticmethod
    def get_instance():
        return rv._registry._get_rv_config()

    def __init__(self,
                 profile=None,
                 rv_home=None,
                 config_overrides=None,
                 tmp_dir=None,
                 rm_tmp_dir=False):

        if tmp_dir is not None:
            self.set_tmp_dir(tmp_dir, rm_tmp_dir)

        if profile is None:
            if os.environ.get('RV_PROFILE'):
                profile = os.environ.get('RV_PROFILE')
            else:
                profile = RVConfig.DEFAULT_PROFILE

        if config_overrides is None:
            config_overrides = {}

        if rv_home is None:
            home = os.path.expanduser('~')
            rv_home = os.path.join(home, '.rastervision')
        self.rv_home = rv_home

        config_file_locations = self._discover_config_file_locations(profile)

        help_doc = ('Check '
                    'https://rastervision.readthedocs.io/configuration '
                    'for docs.')
        self.config = ConfigManager(
            # Specify one or more configuration environments in
            # the order they should be checked
            [
                # Allow overrides
                ConfigDictEnv(config_overrides),

                # Looks in OS environment first
                ConfigOSEnv(),

                # Look for an .env file
                ConfigEnvFileEnv('.env'),

                # Looks in INI files in order specified
                ConfigIniEnv(config_file_locations),
            ],

            # Make it easy for users to find your configuration docs
            doc=help_doc)

    def _discover_config_file_locations(self, profile):
        result = []

        # Allow for user to specify specific config file
        # in the RASTERVISION_CONFIG env variable.
        env_specified_path = os.environ.get('RV_CONFIG')
        if env_specified_path:
            result.append(env_specified_path)

        # Allow user to specify config directory that will
        # contain profile configs in RASTERVISION_CONFIG_DIR
        # env variable. Otherwise, use "$HOME/.rastervision"
        env_specified_dir_path = os.environ.get('RV_CONFIG_DIR')
        if env_specified_dir_path:
            result.append(os.path.join(env_specified_dir_path, profile))
        else:
            result.append(os.path.join(self.rv_home, profile))
        result.append(os.path.join(os.getcwd(), '.rastervision'))

        # Filter out any that do not exist.
        results_that_exist = list(filter(lambda x: os.path.exists(x), result))

        # If the profile is not default, and there is no config that exists,
        # then throw an error.
        if not any(results_that_exist) and profile != RVConfig.DEFAULT_PROFILE:
            raise rv.ConfigError('Configuration Profile {} not found. '
                                 'Checked: {}'.format(profile,
                                                      ', '.join(result)))

        return results_that_exist

    def get_subconfig(self, namespace):
        return self.config.with_namespace(namespace)

    def get_model_defaults(self):
        """Return the "model defaults"

        The model defaults is a json file that lists a set of default
        configurations for models, per backend and model key.
        There are defaults that are installed with Raster Vision, but
        users can override these defaults with their own by setting
        the "model_defaults_uri" in the [RV] section of
        thier configuration file, or by setting the RV_MODEL_DEFAULT_URI
        environment variable.
        """
        subconfig = self.get_subconfig('RV')
        default_path = os.path.join(
            os.path.dirname(rv.backend.__file__), 'model_defaults.json')
        model_default_uri = subconfig(
            'model_defaults_uri', default=default_path)

        model_defaults = json.loads(file_to_str(model_default_uri))

        return model_defaults
