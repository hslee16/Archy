# Governance

archy uses a Benevolent Dictator For Life (BDFL) model with one maintainer (see [`MAINTAINERS.md`](MAINTAINERS.md)). This document describes how decisions get made, what users and contributors can expect, and how that will evolve.

## How decisions are made

For now: the maintainer decides. The decision surface is small enough that an explicit voting or RFC process would add friction without adding quality. Decisions of consequence (scope changes, breaking API changes, new score axes, new MCP tools, license changes) are made publicly through GitHub issues and the [`docs/ROADMAP.md`](docs/ROADMAP.md) file before code lands. Implementation decisions land in PRs with reasoning in the description or in `docs/LEARNINGS.md`.

## What the maintainer commits to

- **Public roadmap.** What is planned, what is deferred, what is rejected, and why, lives in [`docs/ROADMAP.md`](docs/ROADMAP.md) and [`docs/FUTURE.md`](docs/FUTURE.md).
- **No surprise re-licensing.** archy is MIT licensed. If the license ever changes (it is not planned to), the change will be announced in advance with rationale; existing released versions remain under their original license.
- **No commercial-feature gating in the OSS package.** If a hosted version of archy ever exists, it will not have a feature the OSS package lacks. The OSS package is the product.
- **Response targets.** Best-effort on issues within a week. Security reports get priority; see the security policy in the repo for the disclosure flow when one exists.

## What contributors are asked to do

- Open an issue before a large PR so we can scope-check before you invest time.
- Follow `CONTRIBUTING.md` (notably: no em-dash characters anywhere in the repo).
- Be patient with review cadence; the maintainer is one person with a day job.

## When this will change

When archy has two or more regular committers we will switch to an explicit committer / maintainer ladder, drop the BDFL model in favor of consensus-with-lazy-approval, and move the on/off-boarding criteria into this file. Until then this document stays simple.

## Right to fork

archy is MIT licensed. Anyone may fork at any time for any reason, including disagreement with this project's direction. That is a feature, not a failure mode.
