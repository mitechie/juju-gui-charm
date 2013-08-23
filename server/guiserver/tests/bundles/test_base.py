# This file is part of the Juju GUI, which lets users view and manage Juju
# environments within a graphical interface (https://launchpad.net/juju-gui).
# Copyright (C) 2013 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License version 3, as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Tests for the bundle deployment base objects."""

import mock
from tornado import gen
from tornado.testing import(
    AsyncTestCase,
    gen_test,
)

from guiserver import auth
from guiserver.bundles import (
    base,
    utils,
)
from guiserver.tests import helpers


@mock.patch('time.time', mock.Mock(return_value=42))
class TestDeployer(helpers.BundlesTestMixin, AsyncTestCase):

    bundle = {'foo': 'bar'}
    user = auth.User(
        username='myuser', password='mypasswd', is_authenticated=True)

    def patch_validate(self, side_effect=None):
        """Mock the blocking validate function."""
        mock_validate = helpers.MultiProcessMock(side_effect=side_effect)
        return mock.patch('guiserver.bundles.blocking.validate', mock_validate)

    def patch_import_bundle(self, side_effect=None):
        """Mock the blocking import_bundle function."""
        mock_import_bundle = helpers.MultiProcessMock(side_effect=side_effect)
        import_bundle_path = 'guiserver.bundles.blocking.import_bundle'
        return mock.patch(import_bundle_path, mock_import_bundle)

    def assert_change(
            self, changes, deployment_id, status, queue=None, error=None):
        """Ensure only one change is present in the given changes.

        Also check the change refers to the expected deployment id and status.
        Optionally also ensure the change includes the queue and error values.
        """
        self.assertEqual(1, len(changes))
        expected = {
            'DeploymentId': deployment_id,
            'Status': status,
            'Time': 42,
        }
        if queue is not None:
            expected['Queue'] = queue
        if error is not None:
            expected['Error'] = error
        self.assertEqual(expected, changes[0])

    @gen_test
    def test_validation_success(self):
        # None is returned if the validation succeeds.
        deployer = self.make_deployer()
        with self.patch_validate():
            result = yield deployer.validate(self.user, 'bundle', self.bundle)
        self.assertIsNone(result)

    @gen_test
    def test_validation_failure(self):
        # An error message is returned if the validation fails.
        deployer = self.make_deployer()
        error = ValueError('validation error')
        with self.patch_validate(side_effect=error):
            result = yield deployer.validate(self.user, 'bundle', self.bundle)
        self.assertEqual(str(error), result)

    @gen_test
    def test_validation_process(self):
        # The validation is executed in a separate process.
        deployer = self.make_deployer()
        with self.patch_validate() as mock_validate:
            yield deployer.validate(self.user, 'bundle', self.bundle)
        mock_validate.assert_called_once_with(
            self.apiurl, self.user.password, self.bundle)
        mock_validate.assert_called_in_a_separate_process()

    @gen_test
    def test_unsupported_api_version(self):
        # An error message is returned the API version is not supported.
        deployer = self.make_deployer(apiversion='not-supported')
        result = yield deployer.validate(self.user, 'bundle', self.bundle)
        self.assertEqual('unsupported API version', result)

    def test_import_bundle_scheduling(self):
        # A deployment id is returned if the bundle import process is
        # successfully scheduled.
        deployer = self.make_deployer()
        with self.patch_import_bundle():
            deployment_id = deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        self.assertIsInstance(deployment_id, int)
        # Wait for the deployment to be completed.
        self.wait()

    def test_import_bundle_process(self):
        # The deployment is executed in a separate process.
        deployer = self.make_deployer()
        with self.patch_import_bundle() as mock_import_bundle:
            deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        # Wait for the deployment to be completed.
        self.wait()
        mock_import_bundle.assert_called_once_with(
            self.apiurl, self.user.password, 'bundle', self.bundle)
        mock_import_bundle.assert_called_in_a_separate_process()

    def test_watch(self):
        # To start observing a deployment progress, a client can obtain a
        # watcher id for the given deployment job.
        deployer = self.make_deployer()
        with self.patch_import_bundle():
            deployment_id = deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        watcher_id = deployer.watch(deployment_id)
        self.assertIsInstance(watcher_id, int)
        # Wait for the deployment to be completed.
        self.wait()

    def test_watch_unknown_deployment(self):
        # None is returned if a client tries to observe an invalid deployment.
        deployer = self.make_deployer()
        self.assertIsNone(deployer.watch(42))

    @gen_test
    def test_next(self):
        # A client can be asynchronously notified of deployment changes.
        deployer = self.make_deployer()
        with self.patch_import_bundle():
            deployment_id = deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        watcher_id = deployer.watch(deployment_id)
        # A first change is received notifying that the deployment is started.
        changes = yield deployer.next(watcher_id)
        self.assert_change(changes, deployment_id, utils.STARTED, queue=0)
        # A second change is received notifying a completed deployment.
        changes = yield deployer.next(watcher_id)
        self.assert_change(changes, deployment_id, utils.COMPLETED)
        # Only the last change is notified to new subscribers.
        watcher_id = deployer.watch(deployment_id)
        changes = yield deployer.next(watcher_id)
        self.assert_change(changes, deployment_id, utils.COMPLETED)
        # Wait for the deployment to be completed.
        self.wait()

    @gen_test
    def test_multiple_deployments(self):
        # Multiple deployments can be scheduled and observed.
        deployer = self.make_deployer()
        with self.patch_import_bundle():
            deployment1 = deployer.import_bundle(
                self.user, 'bundle', self.bundle)
            deployment2 = deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        watcher1 = deployer.watch(deployment1)
        watcher2 = deployer.watch(deployment2)
        # The first deployment is started.
        changes = yield deployer.next(watcher1)
        self.assert_change(changes, deployment1, utils.STARTED, queue=0)
        # The second deployment is scheduled and will only start after the
        # first one is done.
        changes = yield deployer.next(watcher2)
        self.assert_change(changes, deployment2, utils.SCHEDULED, queue=1)
        # The first deployment completes.
        changes = yield deployer.next(watcher1)
        self.assert_change(changes, deployment1, utils.COMPLETED)
        # The second one is started.
        changes = yield deployer.next(watcher2)
        self.assert_change(changes, deployment2, utils.STARTED, queue=0)
        # Wait for the deployment to be completed.
        self.wait()

    @gen_test
    def test_deployment_failure(self):
        # An error change is notified if the deployment process fails.
        deployer = self.make_deployer()
        with self.patch_import_bundle(side_effect=RuntimeError('bad wolf')):
            deployment_id = deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        watcher_id = deployer.watch(deployment_id)
        # We expect two changes: the second one should include the error.
        yield deployer.next(watcher_id)
        changes = yield deployer.next(watcher_id)
        self.assert_change(
            changes, deployment_id, utils.COMPLETED, error='bad wolf')
        # Wait for the deployment to be completed.
        self.wait()

    @gen_test
    def test_invalid_watcher(self):
        # None is returned if the watcher id is not valid.
        deployer = self.make_deployer()
        changes = deployer.next(42)
        self.assertIsNone(changes)

    def test_initial_status(self):
        # The initial deployer status is an empty list.
        deployer = self.make_deployer()
        self.assertEqual([], deployer.status())

    def test_status(self):
        # The status contains the last known change for each deployment.
        deployer = self.make_deployer()
        with self.patch_import_bundle():
            deployment1 = deployer.import_bundle(
                self.user, 'bundle', self.bundle)
            deployment2 = deployer.import_bundle(
                self.user, 'bundle', self.bundle, callback=self.stop)
        # Wait for the deployment to be completed.
        self.wait()
        # At this point we expect two completed deployments.
        change1, change2 = deployer.status()
        self.assertEqual(utils.COMPLETED, change1['Status'])
        self.assertEqual(utils.COMPLETED, change2['Status'])
        self.assertEqual(deployment1, change1['DeploymentId'])
        self.assertEqual(deployment2, change2['DeploymentId'])


class TestDeployMiddleware(helpers.BundlesTestMixin, AsyncTestCase):

    def setUp(self):
        # Create a DeployMiddleware instance.
        super(TestDeployMiddleware, self).setUp()
        self.user = auth.User(
            username='myuser', password='mypasswd', is_authenticated=True)
        self.deployer = self.make_deployer()
        self.responses = []
        self.deployment = base.DeployMiddleware(
            self.user, self.deployer, self.responses.append)

    def test_deployment_requested(self):
        # True is returned if the incoming data is a deployment request.
        requests = (
            self.make_deployment_request('Import'),
            self.make_deployment_request('Watch'),
            self.make_deployment_request('Next'),
            self.make_deployment_request('Status'),
        )
        for request in requests:
            requested = self.deployment.requested(request)
            self.assertTrue(requested, request)

    def test_deployment_not_requested(self):
        # False is returned if the incoming data is not a deployment request.
        # Params are not validated by DeployMiddleware.requested.
        params = {'Name': 'mybundle', 'YAML': 'foo: bar'}
        requests = (
            # Empty request.
            {},
            # Invalid type field.
            {
                'RequestId': 1,
                'Type': 'INVALID',
                'Request': 'Import',
                'Params': params,
            },
            # Invalid request field.
            {
                'RequestId': 2,
                'Type': 'Deployer',
                'Request': 'INVALID',
                'Params': params,
            },
            # Missing request id field.
            {
                'INVALID': 3,
                'Type': 'Deployer',
                'Request': 'Import',
                'Params': params,
            },
            # Field names are case sensitive.
            {
                'RequestId': 4,
                'type': 'Deployer',
                'request': 'Import',
                'Params': params,
            },
        )
        for request in requests:
            requested = self.deployment.requested(request)
            self.assertFalse(requested, request)

    @gen_test
    def test_process_request(self):
        # A deployment request is correctly processed.
        deployment_request = self.make_deployment_request('Import')

        @gen.coroutine
        def view(request, deployer):
            # Ensure the view is called with the expected arguments.
            self.assertEqual(deployment_request['Params'], request.params)
            self.assertIs(self.user, request.user)
            self.assertIs(self.deployer, deployer)
            return {'Response': 'ok'}

        # Patch the routes so that the customized view defined above is called
        # when an import request is processed.
        self.deployment.routes['Import'] = view
        yield self.deployment.process_request(deployment_request)
        # Ensure the response has been correctly sent.
        self.assertEqual(1, len(self.responses))
        response = self.responses[0]
        self.assertEqual({'RequestId': 42, 'Response': 'ok'}, response)
