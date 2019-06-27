# -*- mode: python; python-indent: 4 -*-
import datetime
from multiprocessing.pool import ThreadPool
from resource_manager import id_allocator
import ncs
from ncs.experimental import Subscriber
from ncs.application import Service
from ncs.application import PlanComponent
from ncs.dp import Action
from sr_migrate.disable_ldp import disable_ldp
from sr_migrate.router_test import RouterTest, to_result


def set_plan_reached(service, router, state):
    service.plan.component[router.name].state[state].status = 'reached'

def check_test_results(results, state):
    if results.self_test_result is None:
        return False

    for router in results.router:
        ncs.maagic.cd(results, '..').plan.component[router.device_name].state[
            state].status = 'reached' if (
                router.router_test_result == 'PASS' or
                results.self_test_result == 'PASS') else 'failed'

    return results.self_test_result == 'PASS'

# ------------------------
# SERVICE CALLBACK EXAMPLE
# ------------------------
class ServiceCallbacks(Service):

    # The create() callback is invoked inside NCS FASTMAP and
    # must always exist.
    @Service.create
    def cb_create(self, tctx, root, service, proplist):
        self.log.info('Service create(service=', service._path, ')')

        self_plan = PlanComponent(service, 'self', 'ncs:self')
        self_plan.append_state('ncs:init')
        self_plan.append_state('ncs:ready')
        self_plan.set_reached('ncs:init')

        ready_count = 0
        igp_domain = root.sr_migrate__igp_domain[service.igp_domain]

        for router in igp_domain.router:
            router_plan = PlanComponent(service, router.name,
                                        'sr-migrate:router-migration')
            router_plan.append_state('ncs:init')
            router_plan.append_state('sr-migrate:segment-routing-enabled')
            router_plan.append_state('sr-migrate:connectivity-test')
            router_plan.append_state('sr-migrate:sr-imposition-preferred')
            router_plan.append_state('sr-migrate:label-imposition-test')
            router_plan.append_state('sr-migrate:ldp-disabled')
            router_plan.append_state('ncs:ready')
            router_plan.set_reached('ncs:init')

            requested_prefix_sid = -1
            if router.custom_prefix_sid:
                requested_prefix_sid = router.custom_prefix_sid

            allocation_name = '%s-%s' % (service.igp_domain, router.name)

            id_allocator.id_request(
                service, "/ncs:services/sr-migrate:sr-migrate" +
                "[sr-migrate:igp-domain='%s']" % service.igp_domain,
                tctx.username, igp_domain.sid_pool, allocation_name, False,
                requested_prefix_sid
            )

            prefix_sid = id_allocator.id_read(
                tctx.username, root, igp_domain.sid_pool, allocation_name)

            if not prefix_sid:
                self.log.info('Prefix-sid for router %s not ready' %
                              router.name)
                continue

            self.log.info('Allocated prefix-sid %d for router %s' %
                          (prefix_sid, router.name))
            router.prefix_sid = prefix_sid

            if service.enable_segment_routing:
                template = ncs.template.Template(router)
                template.apply('enable-segment-routing')
                router_plan.set_reached('sr-migrate:segment-routing-enabled')
                ready_count += 1

        if ready_count != len(igp_domain.router.keys()):
            return

        igp_domain.sr_migrate_test_request.create('connectivity-test')

        if not check_test_results(service.connectivity_test_results,
                                  'sr-migrate:connectivity-test'):
            self.log.info('Connectivity test not passed')
            return

        if service.prefer_sr_imposition:
            for router in igp_domain.router:
                template = ncs.template.Template(router)
                template.apply('prefer-sr-imposition')
                set_plan_reached(service, router,
                                 'sr-migrate:sr-imposition-preferred')

            igp_domain.sr_migrate_test_request.create('label-imposition-test')
            if not check_test_results(service.label_imposition_test_results,
                                      'sr-migrate:label-imposition-test'):
                self.log.info('Label imposition test not passed')
                return

            if service.disable_ldp:
                for router in igp_domain.router:
                    disable_ldp(root, igp_domain)
                    set_plan_reached(service, router, 'sr-migrate:ldp-disabled')
                    set_plan_reached(service, router, 'ncs:ready')

                self_plan.set_reached('ncs:ready')
                self.log.info('Service ready - %s migration complete' %
                              (service.igp_domain))

    # The pre_modification() and post_modification() callbacks are optional,
    # and are invoked outside FASTMAP. pre_modification() is invoked before
    # create, update, or delete of the service, as indicated by the enum
    # ncs_service_operation op parameter. Conversely
    # post_modification() is invoked after create, update, or delete
    # of the service. These functions can be useful e.g. for
    # allocations that should be stored and existing also when the
    # service instance is removed.

    # @Service.pre_lock_create
    # def cb_pre_lock_create(self, tctx, root, service, proplist):
    #     self.log.info('Service plcreate(service=', service._path, ')')

    # @Service.pre_modification
    # def cb_pre_modification(self, tctx, op, kp, root, proplist):
    #     self.log.info('Service premod(service=', kp, ')')

    # @Service.post_modification
    # def cb_post_modification(self, tctx, op, kp, root, proplist):
    #     self.log.info('Service premod(service=', kp, ')')


class MigrateTest(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output):
        self.log.info('action name: ', name)

        with ncs.maapi.single_write_trans(uinfo.username, 'python') as th:
            root = ncs.maagic.get_root(th)
            service = ncs.maagic.get_node(th, kp)
            igp_domain = root.sr_migrate__igp_domain[service.igp_domain]

            result = service.connectivity_test_results
            if input.include_cef_tests:
                result = service.label_imposition_test_results

            # Reset results
            del result.router
            del result.self_test_result
            del result.plan.component
            del result.plan_history.plan

            if result.plan.failed:
                del result.plan.failed

            if result.plan.commit_queue:
                del result.plan.commit_queue

            if result.plan.error_info:
                del result.plan.error_info

            result.date_and_time = datetime.datetime.now().isoformat()

            options = (input.check_frr_sids_are_protected,
                       input.include_cef_tests, input.include_ping_tests)

            tests = [RouterTest(self.log, router, options, result,
                                [destination_router
                                 for destination_router in igp_domain.router
                                 if destination_router.name != router.name])
                     for router in igp_domain.router]
            th.apply()

        if input.multi_thread:
            pool = ThreadPool(len(tests))
            threads = [pool.apply_async(test.run) for test in tests]
            output.success = all([thread.get() for thread in threads])
        else:
            output.success = all([test.run() for test in tests])

        with ncs.maapi.single_write_trans(uinfo.username, 'python') as th:
            result = ncs.maagic.get_node(th, result._path)
            result.self_test_result = to_result(output.success)
            th.apply()


class IgpDomainSubscriber(Subscriber):
    #pylint: disable=no-self-use
    #pylint: disable=unused-argument

    def init(self):
        self.register('/sr-migrate:igp-domain/sr-migrate-test-request')

    def pre_iterate(self):
        return []

    def iterate(self, keypath, op, oldval, newval, state):
        if op is ncs.MOP_CREATED:
            state.append((str(keypath[2:]), keypath[0][0]))
        return ncs.ITER_RECURSE

    def post_iterate(self, state):
        for request in state:
            self.run_test_and_redeploy(request[0], request[1])

    def should_post_iterate(self, state):
        return state != []

    def run_test_and_redeploy(self, igp_domain_keypath, request_enum_hash):
        with ncs.maapi.single_read_trans('admin', 'python') as th:
            root = ncs.maagic.get_root(th)
            igp_domain = ncs.maagic.get_node(th, igp_domain_keypath)

            #pylint: disable=protected-access
            request_name = ncs.Value(
                request_enum_hash, ncs.C_ENUM_HASH).val2str(
                    ncs.maagic._tm.get_leaf_list_type(
                        igp_domain.sr_migrate_test_request._cs_node))

            self.log.info('New migrate test request: %s' % request_name)
            if igp_domain.name not in root.ncs__services.sr_migrate__sr_migrate:
                self.log.error("Test request %s ignored. " % request_name +
                               "Migrate service doesn't exist")
                return

            service = root.services.sr_migrate__sr_migrate[igp_domain.name]

            test_input = service.self_test.get_input()
            if request_name == 'label-imposition-test':
                test_input.include_cef_tests = True

            service.self_test(test_input)
            service.re_deploy()


# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        # The application class sets up logging for us. It is accessible
        # through 'self.log' and is a ncs.log.Log instance.
        self.log.info('Main RUNNING')

        # Service callbacks require a registration for a 'service point',
        # as specified in the corresponding data model.
        #
        self.register_service('sr-migrate', ServiceCallbacks)

        # When using actions, this is how we register them:
        #
        self.register_action('sr-migrate-test', MigrateTest)

        # Create your subscriber
        #pylint: disable=attribute-defined-outside-init
        self.sub = IgpDomainSubscriber(app=self)
        self.sub.start()

        # If we registered any callback(s) above, the Application class
        # took care of creating a daemon (related to the service/action point).

        # When this setup method is finished, all registrations are
        # considered done and the application is 'started'.

    def teardown(self):
        # When the application is finished (which would happen if NCS went
        # down, packages were reloaded or some error occurred) this teardown
        # method will be called.

        self.sub.stop()
        self.log.info('Main FINISHED')
