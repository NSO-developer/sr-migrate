# Migrating to Segment Routing using NSO

This demo shows how NSO can be used to safely automate the process of migrating
an IGP domain from LDP to SR. To do this, NSO will run a series of operational
tests on the routers in the IGP domain to confirm connectivity at each stage
of the migration process.

**The demo only supports IOS XR devices running ISIS.**

## The migration process
Segment Routing and LDP can coexist together and by default LDP is preferred
over SR. Therefore NSO can provision the initial SR configuration without
breaking the existing LDP connectivity.

### Step 1: Enable segment routing
Initially, NSO will configure each router in the IGP domain to enable segment
routing. The configuration includes:

- The Segment Routing Global Block (SRGB).
- A unique prefix SID on the loopback interface.
- TI-LFA enabled on all router interfaces.

After the configuration has been applied, NSO will run a series of connectivity
tests. For each router in the IGP domain, connectivity to every other router
in the domain is validated as follows:

1. **Adjacency SID validation.** If the router is in the neighbours list, the
adjacency SIDs are checked. For each adjacency SID, the corresponding MPLS
forwarding labels are validated. The outgoing label and outgoing interface
must be correct. For FRR adjacency SIDs, these must be protected (see
connectivity test notes below).

2. **Prefix SID validation.** Next, the ISIS segment routing labels are checked
to ensure the prefix SID has been learnt. The corresponding MPLS forwarding
labels must also be valid.

3. **Ping test.**  Finally, an sr-mpls ping test is executed.

### Step 2: Prefer SR label imposition
Only if all the previous tests succeed, NSO will now configure each router in
the IGP domain to prefer SR over LDP.

NSO will then re-test the connectivity between all the routers in the domain.

4. **CEF validation.** In addition to re-running the previous tests, NSO will
check the prefix SID entry in the Forwarding Information Base to ensure the
SR label is imposed.

### Step 3: Disable LDP
If all of the previous tests have passed, LDP can safely be disabled.
NSO will remove all the LDP interfaces for connections between the routers in
the IGP domain.


## Getting Started
The demo can configure the routers in the IGP domain through either the NETCONF
or CLI interfaces. However, currently **the operational tests are only
supported using the CLI interface**.

### Pre-requisites
The NETCONF NED is included in this repository, but the CLI NED is not,
and **must be copied to the packages directory before starting the demo**.
The CLI NED package directory name must be **cisco-iosxr-cli**.

**The demo will fail to compile and will not work without the CLI NED.**

### Compiling the demo
The packages can be compiled individually and copied to an existing NSO
working directory. Or the supplied Makefile can be used to automatically
compile the packages and set up the NSO directory. To do this, change to demo
directory and run the `all` make target:

    > cd ~/sr-migrate
    > make all

### Running with NETSIM
Use the `start` make target to start both NSO and an example NETSIM environment.
Once NSO has started, synchronise the devices.

    > make start
    > ncs_cli -u admin
    admin@ncs> request devices sync-from

**Note:** When using NETSIM devices, the operational tests will not be executed.

### Running with real devices
If using real devices, NSO should be started directly rather than using the
`start` make target (which would start NETSIM too). Once NSO has started,
real devices can be added and synced in the usual manner.

#### Important - CLI devices
To run all the operational tests, **a CLI device must exist in
NSO for each router in the IGP domain**. A NETCONF version of the device can
also be added so the SR configuration is done through the NETCONF interface.

In this case, when adding both a NETCONF and CLI version of the same router,
the CLI device is only used for executing the operational commands and should
be southbound locked. If there is no corresponding CLI device in NSO, the
operational tests for that router won't be executed. The two devices must have
different names, these can be anything, they are matched on the IP address.


## Demo set-up
The following steps describe the initial configuration required for the demo.
This can be configured in advance. The examples show how to perform the
configuration using the NSO CLI interface, however the NSO Web UI can be used
if preferred.

### Segment Routing Global Block
The SRGB range (which will be configured on each router in the IGP domain) can
be changed by updating the bounds in `/sr-migrate:sr-infrastructure/srgb`:

    admin@ncs% set sr-infrastructure srgb lower-bound 16000 upper-bound 23999

### SID pool
An internal resource-manager id-pool needs to be configured, which the demo
will use to allocate the prefix SIDs from. This does not need to match the
full SRGB, but should be in the SRGB range:

    set resource-pools id-pool pool-1 range start 17000 end 17500

### IGP domain
The IGP domain needs to be created in NSO. This contains the list of routers
in the domain. **The name of the domain must be the ISIS instance name.**
The following parameters should be checked / updated:

**Mandatory**:
- `sid-pool` This should be set to the id-pool created in the previous step.
  For each router in the domain, a prefix SID will be allocated from this pool.

- `router` This is the router list. All routers in the IGP domain should
  be added to this list (these devices need to have been previously added to
  NSO's device list and synced). A mix of NETCONF and CLI devices can be added.

  As noted above, for NETCONF devices, the demo will automatically look for
  the corresponding CLI device in NSO's device list to run the operational
  tests (**don't add both devices to this sr-migrate router list**).

  For each router, a `custom-prefix-sid` can be requested. If this is not
  specified, a SID is automatically allocated from the SID pool.

        admin@ncs> config
        admin@ncs% edit igp-domain 1
        admin@ncs% set sid-pool pool-1
        admin@ncs% set router cli-p0
        admin@ncs% set router netconf-p1 custom-prefix-sid 17001
        admin@ncs% commit

**Optional**:
- `loopback` This is the loopback interface number where the prefix SID
  will be configured on each router in the domain (the default is 0).

- `address-family` This is used for the TI-LFA configuration on each
  interface (the default is ipv4).


## Migrating the IGP domain
The migration is done by the `sr-migrate` service. The service should be
created using the NSO Web UI so the plan viewer can be shown. This is used to
track the progress of the migration for each router in the domain. A separate
plan viewer is also used to show the results of the connectivity tests as
they execute.

The three migration steps (outlined above) are controlled using corresponding
`enable-segment-routing`, `prefer-sr-imposition` and `disable-ldp` leafs in
the `sr-migrate` service. Each of these can be set invididually so the
configuration generated by NSO at each stage can be previewed using the
dry-run feature. Or all three can be set at once and NSO will automatically
step through the entire process with no manual intervention required.

1. Create the `sr-migrate` service. Choose the igp-domain to migrate (created
   previously).

2. It is recommended to commit at this point (navigate to the Commit Manager
   and press the *Commit* button). NSO will now allocate the prefix SIDs for
   the IGP domain routers, and create the initial plan view.  Navigate to the
   igp-domain in the Configuration Editor to see the allocated SIDs.

   Click on the `sr-migrate` service in the Service Manager to see the plan
   viewer (make sure *hide operational data* is unticked in the *View
   options* menu, and that the *include subfolders* toggle button is
   selected).

   The plan viewer for the connectivity tests will be updated as the tests
   execute, so to monitor these easily, the `connectivity-test-results` and
   `label-imposition-test-results` folders can be opened in new browser tabs.

4. Set the `enable-segment-routing` leaf to true. Navigate to the Commit
   Manager. Look at the *config* tab to see the configuration that will be
   applied to all the routers in the IGP domain. Look at the *native config*
   tab to see the actual NETCONF and CLI that will be generated. Click
   *Commit*.

   Once the commit is complete, switch to the `connectivity-test-results` tab
   to see the plan viewer update as the connectivity tests are executed.

   Once the tests are complete, switch back to the service to see the
   migration plan update.

5. Next, NSO needs to configure the sr-prefer option on each router in the
   IGP domain. Repeat step 3, this time setting the `prefer-sr-imposition` leaf
   to true, and looking at the `label-imposition-test-results` tab.

6. Finally, once all the tests have completed, set the `disable-ldp` leaf to
   true. Switch to the Commit Manager to view and commit the configuration.
   Switch back to the service to see the migration plan viewer shows the
   migration is complete (all green).


### Notes on the connectivity tests
As noted above, the connectivity tests require CLI devices in the NSO device
list for each router in IGP domain.

The tests can be re-ran by invoking the `self-test` action on the service
(untick the *hide actions* option from the *View options* menu if executing
from the Web UI). When executing the test manually, the following inputs
can be set:

- `multi-thread` This will test each router in parallel. This means the
  tests will execute much faster. The default is false to give time to
  switch to the test plan viewer to see the tests execute before they all
  complete.

- `check-frr-sids-are-protected` The default is true and will cause the
  adjacency SIDs test to fail if the FRR SIDs are not marked as protected.
  The TI-LFA configuration is applied to all interfaces so they should always
  be protected.

- `include-cef-tests` If set to true, the CEF tests will be included and the
  results will be written to the `label-imposition-test-results` container.
  Otherwise, the CEF tests are omitted and the results are written to the
  `connectivity-test-results` container.

- `include-ping-tests` If the ping tests are too slow, they can be omitted
  by setting this leaf to false. The default is true.

**Note:** Re-running the test won't cause the `sr-migrate` service to automatically
continue. It should be manually re-deployed if the test result has changed.
