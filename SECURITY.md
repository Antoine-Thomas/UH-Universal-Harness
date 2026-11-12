# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.8.x | yes |
| 0.7.x | security fixes only |
| < 0.7 | no |

UH is pre-1.0 and maintained by one person: the table describes intent, not an
SLA.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private channel: *Security* tab of this repository → *Report a
vulnerability* (GitHub Security Advisories). The report stays private until a fix
is published, and it gives us a place to coordinate the disclosure.

Please include:

- what you did, what you expected, what happened (a minimal reproduction);
- the version (`uh --version`) and the platform;
- whether a secret could have leaked (provider key, web token, session content)
  and, if so, **which one was exposed — never paste the value itself**.

## What is in scope

- key handling: storage in `$UH_HOME/keys.toml`, absence of keys in logs, API
  responses, traces and error messages;
- the local web interface: token handling, `Host`/`Origin` checks, CSP,
  loopback-only defaults, the terminal toolset being off unless enabled;
- the toolsets: path confinement of `file`, the `terminal` blacklist, and the
  fact that a tool error is returned to the model rather than raised;
- the publishing workflow: OIDC trusted publishing, no secret in the repository,
  no long-lived token;
- dependency confusion and supply chain: no runtime dependency besides `httpx`,
  optional extras declared explicitly.

## What is out of scope

- vulnerabilities inside third-party providers or models reachable through the
  router (report them upstream);
- the consequences of running with `--allow-terminal`, `--host 0.0.0.0` or a
  custom provider URL: those are documented opt-ins with stated risks;
- prompt injection producing a bad answer — that is a model behaviour, not a
  memory-safety or secret-leak issue (a tool call that escapes the workspace *is*
  in scope).

## Our side

Best effort, single maintainer: acknowledgement within about a week, a fix or a
mitigation plan in the next patch release, credit in the release notes if you
want it. If a key may have leaked, rotate it first — the tool cannot rotate it
for you.
