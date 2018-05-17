import unittest
import unittest.mock
import os
import io
import sys
from contextlib import redirect_stdout

sys.path.append(os.path.dirname(__file__))
import docker_cache
import build as build_util

DOCKERFILE_DIR = 'docker'

class TestDockerCache(unittest.TestCase):
    def setUp(self):
        docker_cache._compile_upload_cache_file = unittest.mock.MagicMock()

        # We need to be in the same directory than the script so the commands in the dockerfiles work as
        # expected. But the script can be invoked from a different path
        base = os.path.split(os.path.realpath(__file__))[0]
        os.chdir(base)

    def test_full_cache(self):
        """
        Test whether it's possible to restore cache entirely
        :return:
        """
        # Build
        dockerfile_content = """
        FROM busybox
        RUN touch ~/file1
        RUN touch ~/file2
        RUN touch ~/file3
        RUN touch ~/file4
        """
        platform = 'test_full_cache'
        docker_tag = build_util.get_docker_tag(platform=platform)
        dockerfile_path = os.path.join(DOCKERFILE_DIR, 'Dockerfile.build.' + platform)
        try:
            with open(dockerfile_path, 'w') as dockerfile_handle:
                dockerfile_handle.write(dockerfile_content)

            # Warmup
            docker_cache.delete_local_docker_cache(docker_tag=docker_tag)
            with io.StringIO() as buf, redirect_stdout(buf):
                build_util.build_docker(docker_binary='docker', platform=platform)
                output = buf.getvalue()
                assert output.count('Running in') == 4
                assert output.count('Using cache') == 0

            # Assert local cache is properly primed
            with io.StringIO() as buf, redirect_stdout(buf):
                build_util.build_docker(docker_binary='docker', platform=platform)
                output = buf.getvalue()
                assert output.count('Running in') == 0
                assert output.count('Using cache') == 4

            # Upload and clean local cache
            docker_cache.build_save_containers(platforms=[platform], bucket='', load_cache=False)
            docker_cache.delete_local_docker_cache(docker_tag=docker_tag)

            # Build with clean local cache and cache loading enabled
            with io.StringIO() as buf, redirect_stdout(buf):
                docker_cache.build_save_containers(platforms=[platform], bucket='', load_cache=True)
                output = buf.getvalue()
                assert output.count('Running in') == 0
                assert output.count('Using cache') == 4

        finally:
            # Delete dockerfile
            os.remove(dockerfile_path)
            docker_cache.delete_local_docker_cache(docker_tag=docker_tag)



    def test_partial_cache(self):
        """
        Test whether it's possible to restore cache and then pit it up partially by using a Dockerfile which shares
        some parts
        :return:
        """
        pass

    def _assert_output_message(self, func, expected_message, message_appearance_count):
        """
        Assert stdout to check if the expected message have
        :param func Lambda function to call
        :param expected_message:
        :param message_appearance_count:
        :return:
        """