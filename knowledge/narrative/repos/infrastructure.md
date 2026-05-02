# infrastructure

> **Status:** DRAFT — assembled from repo READMEs (top-level + `docs/aws/README.md`); needs PM/eng review.
> Open questions are tagged `<!-- TODO -->`.

## Purpose

The self-service Terraform monorepo for all of MoneyLion's AWS, k8s, Datadog, Snowflake, and related cloud infrastructure. Pod teams provision their own IAM users, S3 buckets, RDS clusters, SQS queues, EKS resources, etc. by adding HCL under their pod folder; **Atlantis** runs `terraform plan`/`apply` on PRs.

For `gru`-managed work this repo matters mostly because **every queue, secret, IAM role, RDS cluster, and Kafka topic that `payment-platform` and `walletapi` depend on is provisioned here** — cross-repo references are by name, not import.

## Owners

- Eng pod: **INFRAML** (Infrastructure and SRE) for shared modules + `platform/`; **per-pod** owners for `pod-*/` subdirectories.
- CODEOWNERS file: `.github/CODEOWNERS` (the source of truth).
- Production approvals: ≥ 1 DevOps approval required; staging + dev are notification-only.
- Atlantis config: `atlantis.yaml` at the repo root (`new_atlantis_yaml_entry.sh` adds new entries).

## Dominant conventions

- **IaC**: Terraform + Atlantis. Almost everything is HCL; helper bash scripts (`new_pod.sh`, `new_rds.sh`, `new_docdb.sh`, `new_service.sh`) scaffold common patterns.
- **Pre-commit**: `pre-commit install` is mandatory for contributors.
- **Folder structure mirrors org structure** (per `docs/STRUCTURE.md`):
  - `pod-<NAME>/` — owned by that pod (e.g. `pod-CORE`, `pod-DE`, `pod-AI`, `pod-CYB`). Pod codes match the [pod glossary in `docs/aws/README.md`](#pod-glossary-cheat-sheet).
  - `platform/` — shared across pods (VPC, network, IAM, shared SQS, shared RDS).
  - `modules/` — reusable terraform modules (`aws/`, `moneylion/`, `mongodb/`, `kafka_connect/`, `datadog/`, etc.). Versioned subpaths (`v1`, `v2`, `v3`, `v4`) — **the version is part of the module path** and changes are deliberate.
  - `mk2acc/`, `engine/`, `evenfinancial/`, `fiona/`, `hifiona/`, `okta/`, `jumpcloud/` — per-AWS-account / per-domain stacks.
  - `network/`, `datadog/`, `aws_sso/`, `aws-configs/` — cross-cutting global config.
- **Environment naming**: `production` / `staging` / `development`, prefixed `prod` / `stag` / `dev` in resource names (`prodrds-balances-cluster`, `stag-mfaapi`, etc.).
- **Resource tagging**: Every resource carries a `pod` tag using the pod codes below — load-bearing for cost allocation and IAM scoping.
- **Two AWS accounts** in heavy use: `moneylion` (production + staging) and `mk2acc` (development). Module placement depends on the target account.
- **k8s clusters**: `stag-eks` and `prod-eks` (under `platform/`); access is via `aws-vault` (see per-cluster READMEs).

## Pod glossary cheat sheet

Lifted from [`docs/aws/README.md`](../../../infrastructure/docs/aws/README.md). Pod codes are tags, IAM scope, and Jira project alignment all in one.

| Code | Description |
|---|---|
| **PLTPM** | **Payments** — owns `payment-platform`, `walletapi`, related infra |
| **CORE** | Core Engineering |
| **BANK** | Banking |
| **LOAN** | Loans |
| **WI** | Wealth & Investments |
| **CARDS** | Cards & Personalization |
| **MEMBER** | Membership (Plus/Bonus) |
| **PFM** | Personal Finance Management |
| **PLTBV** | BV & Bank Connect |
| **PLTBB** | Decision Engine |
| **PLTCUS** | FE WEB |
| **PLTMOB** | Mobile |
| **GA** / **GE** | Growth: Acquisition / Engagement |
| **REW** | Rewards |
| **FRAUD** | Fraud |
| **INFRAML** | Infrastructure and SRE (owns shared `platform/` + `modules/`) |
| **DE** | Data Engineering |
| **AI** | Artificial Intelligence |
| **CA** | Cash Advance |
| **FIN** | Finance |
| **CYB** | CyberSecurity |
| **QDS** | QDS & Operations |

(Full list, including newer pods like `pod-BNPL`, `pod-CDP`, `pod-CRYPTO`, `pod-CM`, `pod-CNF`, `pod-CUSTOMER-OPS`, in the AWS README.)

## What graphify systematically misses

- **Cross-repo references are by string** — every SQS URL, secret ARN, RDS endpoint, and IAM role consumed by `payment-platform` / `walletapi` lives here as a Terraform output, but the consuming Java code references it by name only. Graphify's structural edges won't link the two.
- **Module versions matter.** `modules/aws/eks/v4` and `modules/aws/eks/v1` are separate modules with different inputs/outputs; "the EKS module" is ambiguous without the version path.
- **Atlantis is the only execution path.** Local `terraform apply` is not the workflow — changes are applied via PR. Tooling that simulates changes must use `terraform plan` only.
- **Pod ownership is a soft contract.** A `pod-LOAN` change can still touch `platform/` shared resources; CODEOWNERS catches some cross-pod impact but not all.

## Tribal knowledge

- **`payments-graph` symlink** — `gru` symlinks the whole `infrastructure` repo into `~/payments-graph/infrastructure` for graphify ingestion. Most of the repo is irrelevant to PLTPM tickets; expect noise from `pod-DE`, Snowflake, and other unrelated subtrees in graph queries.
- **Helper scripts encode tribal knowledge.** `new_rds.sh`, `new_docdb.sh`, `new_service.sh`, `new_pod.sh` exist because the equivalent manual steps are easy to miss. Prefer the scripts over hand-writing the HCL for those resources.
- **`docs/aws/README.md` doubles as a worked-examples cookbook** — the IAM, S3, RDS, SQS, ALB, DocumentDB sections are the fastest way to onboard.
- **`payment-platform` uses RDS IAM auth.** The Aurora module + IAM role wiring for that lives here; if IAM auth breaks for the service, this repo is the first place to look.
- **Wells Fargo + Payliance ingress is provisioned here** — the SQS queues that `payment-platform`'s `PaylianceReturnProcessTask` and `WellsFargoReturnProcessTask` consume. <!-- TODO: confirm which exact module(s) provision these — likely under `pod-PLTPM/` or `platform/sqs/` -->
- **Datadog dashboards / monitors / synthetics** are codified under `datadog/` and `modules/datadog/`. Adding observability for a new payments feature usually requires a PR here.
- **Kafka Connect connectors** (Debezium MySQL/Postgres, Snowflake source, MongoDB DocDB source) live under `modules/kafka_connect/` — relevant when a payments feature needs CDC.
