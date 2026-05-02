# spring-boot-starters

> **Status:** DRAFT — assembled from repo README + module list; needs PM/eng review.
> Open questions are tagged `<!-- TODO -->`.

## Purpose

The shared **dependency-management BOM + Spring Boot starter library** for every JVM service at MoneyLion. Two roles in one repo:

1. **Dependency management** — a single Maven / Gradle artifact (`com.moneylion.platform:dependencies`) that pins compatible versions of Spring, AWS SDKs, Datadog, Kotlin, Kotest, etc. for every consuming service.
2. **Starters + API client SDKs** — opinionated Spring Boot auto-configurations that wire up SQS, Kafka, S3, KMS, MongoDB, monitoring, logging, auth, feature flags, web, encryption, Temporal, analytics, plus first-party API clients (`agreement-api-client`, `bv-api-client`, `data-tokenization-api-client`, `email-api-client`, `roartag-api-client`, `user-api-client`).

`payment-platform` and `walletapi` consume this repo as a Maven parent / dependencies BOM; a version bump here propagates broadly.

## Owners

- Eng pod: <!-- TODO — likely CORE / backend-core; confirm via `.github/CODEOWNERS` -->
- **Per-starter ownership** is set in `.github/CODEOWNERS`. Each starter has its own owner; cross-starter changes need each owner's approval.
- Eng lead: <!-- TODO -->
- On-call rotation: <!-- TODO — likely backend-core for unowned starters, per FAQ in README -->

## Dominant conventions

- **Build**: **Gradle** (Kotlin DSL — `settings.gradle.kts`, `build.gradle.kts`). The repo *publishes* Maven artifacts to GitHub Packages but is itself built with Gradle.
- **Languages**: Java or Kotlin per starter. Kotlin starters must add `*-jvm` artifact dependencies for Java consumers (e.g., `kotest-assertions-core-jvm`).
- **Test**: JUnit + Kotest; `make test` runs the suite. `make fmt` for formatting.
- **Spring Boot baseline**: **Spring Boot 4** is current; Spring Boot 3 is legacy and only gets dependency-management updates (no new starters). Java 17+ minimum.
- **Distribution**: GitHub Packages (`https://maven.pkg.github.com/moneylion/spring-boot-starters` for SB4; `spring-boot-3-starter-parent` for SB3 legacy). Consumers need a GitHub classic token with `read:packages` and SSO authorization for the MoneyLion org.
- **Adding a starter**: copy an existing folder, register in `settings.gradle.kts`, add to `dependencies/build.gradle.kts`, add yourself to `.github/CODEOWNERS`. Anyone can add one; **significant or breaking changes to an existing starter require the owner's sign-off**.
- **API clients live alongside starters.** The team's bias: if your API has any consumer outside the immediate team, ship an SDK here. The README lists this as a deliberate choice for retries, auth, pagination, error handling, and version management.

## What graphify systematically misses

- **Cross-repo blast radius is invisible from inside this repo.** A change to `sqs-starter` or `dependencies/` can affect dozens of downstream services. Graphify scoped to this repo won't surface that; rely on `.github/CODEOWNERS` mentions, the version bump PR template, and the Confluence rollout doc <!-- TODO: confirm process -->.
- **Auto-configuration is convention-based.** Starters ship `spring.factories` / `AutoConfiguration.imports` files; consuming services get behavior implicitly when they add the dependency. Graphify will see the classes but not the "this gets activated when X is on the classpath" wiring.
- **Dependency BOM is the actual API surface for most consumers.** The full list lives in `dependencies/build.gradle.kts` — that file is the single most-consulted artifact in the repo, more than any individual starter README.
- **Version compatibility matrix is implicit.** Spring Boot 3 vs. 4 split, Kotlin/JVM target levels, and AWS SDK v1 vs. v2 choices are encoded in Gradle config, not docs.

## Tribal knowledge

- **Token + SSO is the #1 onboarding gotcha.** New engineers consistently get `401`s pulling artifacts because their GitHub classic token isn't SSO-authorized for the MoneyLion org. The README calls it out explicitly: "press `configure SSO` and authorize the token to access Moneylion".
- **Dockerfiles must propagate `GITHUB_PACKAGES_USERNAME` / `GITHUB_PACKAGES_TOKEN`** as build args + env, then copy `settings.xml` into `$MAVEN_CONFIG`. The README has the canonical snippet.
- **Spring Boot 3 → 4 migration is the strategic direction.** SB3 starters are frozen. New work that needs newer starter features should plan an SB4 migration.
- **The GitHub Actions bot account is `moneylion-bot-infra`** with the `GITHUB_PACKAGES_WRITE_TOKEN` secret (also used by `walletapi`'s publishing flow).
- **Starter tests use `test-fixtures` classifier** — see `sqs-starter` for the pattern. Consuming services pull fixtures rather than re-implementing test scaffolding.
- **Maven parent vs. BOM-only**: most consumers use the `maven-starter-parent` (full Spring Boot parent + repos + dependency mgmt); some use BOM-only (`dependencies` artifact imported as `pom` scope). Both patterns are documented; teams pick by preference.
