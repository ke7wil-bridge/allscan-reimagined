# Bridge endpoint collision safeguards (issue #161)

## Scope

The provisioning previews for M17, P25, NXDN, YSF, direct DMR and Zello use
one shared read-only endpoint inventory before installation or reconfiguration.
Their existing transactional installers recalculate and compare preview plans
under the privileged lock. A detected conflict identifies the bridge/service,
network namespace, transport, bind address, and port; the plan is rejected.
The inventory never silently rewrites a legacy or manually managed file.

The admin-only bridge-diagnostics API now supplies an endpointDiagnostics
object. Bridge Settings > Diagnostics displays an expandable inventory and
explicit conflict summary, plus coverage caveats. If the probe is missing or
cannot read the host, the UI reports it as unavailable rather than claiming
there are no collisions. The diagnostic never starts, stops, or restarts audio.

A disposable root can be inspected with:

    python3 scripts/asr-bridge-endpoint-collisions.py --root /path/to/fixture

The script produces JSON and makes no state changes.

## Collision rules

- Two listeners conflict only if transport (TCP/UDP), namespace and local port
  match AND their bind addresses overlap. A wildcard bind overlaps any
  specific address; distinct concrete addresses on the same host do not.
- Remote destinations are recorded as destinations, not listeners. It is safe
  for independent bridges to use the same remote master address/port.
- Docker bridge networking has its own namespace; host-published Docker ports
  consume host endpoints. Docker host-network services are checked through
  active host sockets, and Docker publishing inspection can be unavailable.
- Asterisk node 1999 is the *single*, mutually exclusive ASR Net Bridge.
  Managed Net Bridge modes intentionally share UDP USRP receiver ports
  52000 and 52001. This exception never applies to standard bridges or manual
  Asterisk nodes. The existing stop-before-start switch sequence remains
  responsible for preventing concurrent Net Bridge audio.
- Inactive ASR installation records reserve port assignments even if no
  process listens and the bridge is not visible as a card.
- The inventory reads known Analog_Bridge, MMDVM_Bridge, and gateway INI
  receive keys, manual Asterisk USRP channels, ASR managed bridge cards,
  installation records, host sockets, and Docker host publishes. It is
  intentionally not a general scanner for every third-party configuration
  format or a positive process-owner attribution system.

## Migration, conflicts, and rollback

When the checker finds an unsafe mapping, resolve it through the bridge's
supported provisioning workflow or explicitly reconfigure the *manual*
service that owns the endpoint. ASR does not renumber external configurations.
There is no blanket manual conflict override. The only shared-endpoint
exception is the documented mutually exclusive Net Bridge transport.

A failed preview does not start a bridge. If a later transactional installation
fails, use the existing backed-up rollback flow. Do not restart Asterisk or
reload app_rpt.so to work around a collision. Preserve legacy settings until
the operator can migrate them safely.

## Qualification and limitations

The deterministic self-test covers competing UDP binds; TCP/UDP reuse;
distinct bind addresses and wildcards; Docker namespaces and published ports;
manual Asterisk USRP links; known legacy INI listeners; inactive managed
records; deliberate Net Bridge sharing; destinations; and no-write diagnostics.

Full isolated native ASL3 and real Docker integration tests, real-audio
feedback/duplication QA, and exhaustive third-party file inspection are still
required before deployment. URF-specific provisioning and every service
enable/recovery path are not yet routed through the shared guard. Passing
offline tests does not authorize any production restart, reload, or change.
