---
Created: 2026-11-09
Last-Modified: 2026-11-11
SPDX-FileCopyrightText: 2026-present Arthit Suriyawongkul
SPDX-FileType: DOCUMENTATION
SPDX-License-Identifier: CC0-1.0
---

# Upstream findings

Behaviour of LicenseID or Pitloom met while building the dataset. Not fixed here and not worked around to improve results; hand each one to the owner. Add a line per finding: date, tool and version, input, observed, expected.

| Date | Tool and version | Input | Observed | Expected |
| :--- | :--------------- | :---- | :------- | :------- |
| 2026-11-09 | licenseid 0.8.2 | `https://licenses.nuget.org/MIT%20OR%20Apache-2.0` (percent-encoded expression URL) | unresolved | `Apache-2.0 OR MIT`. Reported: [bact/licenseid#187](https://github.com/bact/licenseid/issues/187). The dataset unquotes the path itself before resolving (derive rule for `nuget_license_url`); drop that workaround once a fixed LicenseID is pinned. Fixed in licenseid 0.8.3 (2026-11-09: now `Apache-2.0 OR MIT` / `license_url` / `resolver_rule`); the workaround is still in the derive rule. |
| 2026-11-10 | licenseid 0.8.3 | `MIT/Apache-2.0` (Cargo's old spelling of `OR`, crates such as winapi) | unresolved (`not an SPDX operator: /`) | `Apache-2.0 OR MIT`. Fixed in licenseid 0.8.4 (2026-11-10: `Apache-2.0 OR MIT` / `separator` / `resolver_rule`; only listed IDs spelt as listed are read, `MIT/X11`, `TCL/TK` and `MIT/Apache2` stay unresolved). Our `classify` still labels the form `nonstandard_compound`, as SPDX has no `/`. |
| 2026-11-09 | packaging 26.3 (not an upstream bug) | `OpenMDW-1.0` | `unknown_id` in the independent classifier (SPDX 3.27 list) while LicenseID 0.8.2 (3.29) resolves it | Known list-version gap; `validate` check V19 reports it as a warning. |
| 2026-11-09 | SBOM-MinBench schema (format gap, not an upstream bug) | nuspec `metadata.license@type` (`expression`, `file`) | A qualifier row in the `license` element has no schema way to be marked as "not a licence value"; the reader overrides it to `semantic_format` `empty`. | A way to mark a qualifier row (for example a distinct role or semantic format), so it is not read as an empty licence; raise it with the schema when it next changes. |
| 2026-11-11 | pitloom 0.24.0 | ONNX file with `graph.name` = `spox_graph`, `producer_name` = `spox` (silero-vad 5.1.2, `silero_vad.onnx`); also `mxnet_converted_model` with an empty `producer_name` (`DeepBeepMeep/MiniMax-H3`) | recorded as the model name | Dropped as an exporter default, like `main_graph`, `torch_jit` and `tf2onnx`. Not reported yet. |
| 2026-11-11 | licenseid 0.8.4 | Licence URLs `https://oss.oracle.com/licenses/CDDL+GPL-1.1` and `http://glassfish.java.net/public/CDDL+GPL_1_1.html` (Maven: `javax.servlet-api`, `javax.annotation-api`, `jersey-spring`) | unresolved | `CDDL-1.1 OR GPL-2.0 WITH Classpath-exception-2.0`: the "1.1" in the URL is the CDDL version, not a GPL version (reviewer's reading, 2026-11-11). A candidate for a URL rule. Not reported yet. |
