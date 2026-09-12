# Acceptance — the cluster over mutual TLS, 2026-09-12

Release under test: overlay `e11b469` (#282 deploy-overlay card regeneration,
#284 mutual-TLS cluster primitive + membership), deployed to zero/one/two/three
by `pipeos deploy-overlay` (two runs each; the deployer had changed). Admin
password: the test one. Driven from the workstation over ssh, per the
pipeos-fleet skill. Every step is the plan posted on #284.

| # | step | result |
|---|------|--------|
| 1 | deploy: verify PASS, selfcheck green, server.crt carries clientAuth (re-issued by tls-init at the webd restart), :443 up, `cluster status` = none, TXT `k` from every peer | PASS on all four |
| 2 | zero `cluster init zero`: cluster of one, saved, verify PASS; the three others listed as lobby candidates | PASS |
| 3 | zero `add two` wrong password → "two refused the join: wrong password"; right password → both lists both CAs, same hash; `call two` → answered by member c2b0; from two `call zero` → answered by member a4e0 | PASS |
| 4 | three added from the dashboard handler (`POST /api/cluster/add` with a login cookie): pushed to two (`taken`); all three show three members, one hash | PASS |
| 5 | one (not a member) called from zero → handshake refused (CERTIFICATE_VERIFY_FAILED); a certificate-less HTTPS client gets /api/state 200 and /api/cluster 401 "sign in first" | PASS |
| 6 | ~~a laptop and a phone opening https://zero.local: padlock~~ struck 2026-09-12: https is not a customer path; the front door is http://zero.local, which opens the dashboard with no warning page (decision: plain http on the LAN, docs/web-wizard.md § Security posture) | n/a |
| 7 | reboot two: back in ~60 s, all green, still a member, cluster.status regenerated at boot, `call two` from zero works | PASS |
| 8 | three's web stopped: `sync` from zero reports c360 unreachable (rc 1); started again: `sync` → same | PASS |
| 9 | zero `remove c360`: three's call to zero → handshake refused (UNKNOWN_CA), three's status shows LIST DIFFERS on both; `add three` again → admitted, same CA | PASS |
| 10 | three `init --force` (a new cluster of one): within a tick zero's and two's readers each report `dropped: [c360]` on their own; three shows as "in cluster 89f7…" in the lobby | PASS |
| 11 | two's web stopped during an add: zero's selfcheck WARNs "member list differs on c2b0"; `pipeos cluster sync` → c2b0=taken; the WARN clears | PASS |
| 12 | verify PASS and selfcheck green on all four; zero/two/three are a cluster of three, one stays out | PASS |

## Notes
- "LIST DIFFERS" shows for one mDNS tick (~10 s) after any edit, until the
  peer re-announces its new `h`. Expected; #212's page should say "updating".
- The known-good WARN on two after the reboot is the normal promotion window.
- Left in place: zero/two/three as cluster `3762aee1…`; one not a member.
