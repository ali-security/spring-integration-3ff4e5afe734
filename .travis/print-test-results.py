#!/usr/bin/env python3
"""Print per-module test totals and every skipped test with its reason.

Gradle's JUnit XML reports record skips without the reason, so the reason is
taken from the test source: an @Ignore/@Disabled on the method or class, or the
project's opt-in long-running-test guards (LongRunningIntegrationTest rule /
@LongRunningTest condition, enabled only when RUN_LONG_INTEGRATION_TESTS=true).
"""
import glob
import os
import re
import xml.etree.ElementTree as ET

GUARDS = [
    ("LongRunningIntegrationTest", "long-running test, runs only when RUN_LONG_INTEGRATION_TESTS=true (upstream nightly-only)"),
    ("@LongRunningTest", "long-running test, runs only when RUN_LONG_INTEGRATION_TESTS=true (upstream nightly-only)"),
    ("RUN_LONG_INTEGRATION_TESTS", "long-running test, runs only when RUN_LONG_INTEGRATION_TESTS=true (upstream nightly-only)"),
]
ANNOT = re.compile(r'@(Ignore|Disabled)(\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\))?')


def find_source(module, classname):
    rel = classname.split("$")[0].replace(".", "/")
    for ext in ("java", "kt", "groovy"):
        for path in glob.glob(os.path.join(module, "src", "test", "*", rel + "." + ext)):
            return path
    return None


KNOWN = {
    "org.springframework.integration.redis.inbound.RedisQueueMessageDrivenEndpointTests":
        "upstream RedisAvailableRule turns any exception into an Assume skip; here setUpTearDown() "
        "(@Before/@After) throws NullPointerException on a null connectionFactory even with Redis "
        "running (upstream test-harness issue, reproduced with Redis up)",
}


def annotation_reason(line, where):
    m = ANNOT.search(line)
    if m:
        return "@%s on %s%s" % (m.group(1), where, (': "%s"' % m.group(3)) if m.group(3) else " (no message)")
    return None


def reason_for(src, classname, method):
    if not src:
        return "source not found"
    lines = open(src, encoding="utf-8", errors="replace").read().splitlines()
    text = "\n".join(lines)
    name = re.sub(r"[\(\[].*$", "", method).strip()
    decl = re.compile(r"(void|fun)\s+`?" + re.escape(name) + r"`?\s*\(")
    window = []
    for i, line in enumerate(lines):
        if decl.search(line):
            for prev in reversed(lines[max(0, i - 8):i]):
                if prev.strip().startswith("}") or prev.strip().endswith(";"):
                    break
                window.append(prev)
            break
    # 1. explicit @Ignore/@Disabled on the method
    for prev in window:
        r = annotation_reason(prev, "method")
        if r:
            return r
    # 2. explicit @Ignore/@Disabled on the class
    cls = re.search(r"^\s*(public\s+|abstract\s+|internal\s+|open\s+)*class\s", text, re.M)
    if cls:
        for prev in reversed(text[:cls.start()].splitlines()[-8:]):
            r = annotation_reason(prev, "class")
            if r:
                return r
    # 3. opt-in long-running guard (method condition or class-level rule)
    if any("@LongRunningTest" in p for p in window):
        return GUARDS[1][1]
    for token, why in GUARDS:
        if token in text:
            return why
    if classname in KNOWN:
        return KNOWN[classname]
    if "BrokerRunning" in text:
        return "BrokerRunning class rule: needs an MQTT broker on localhost:1883, none reachable"
    for prev in window:
        svc = re.search(r"@(\w+)Available\b", prev)
        if svc:
            return ("@%sAvailable rule: skipped via Assume when the %s service is unreachable or the "
                    "test throws" % (svc.group(1), svc.group(1)))
    if "Assume" in text or "assume" in text:
        return "JUnit assumption in test source not met (Assume/assumeTrue)"
    return "skipped by test framework (no annotation found in source)"


total = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
skips = {}
results = []
for module in sorted(d for d in os.listdir(".") if d.startswith("spring-integration-") and os.path.isdir(d)):
    files = glob.glob(os.path.join(module, "build", "test-results", "test", "*.xml"))
    if not files:
        continue
    mod = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
    for f in files:
        root = ET.parse(f).getroot()
        for k in mod:
            mod[k] += int(root.get(k, 0))
        for tc in root.iter("testcase"):
            sk = tc.find("skipped")
            if sk is not None:
                cn = tc.get("classname")
                msg = sk.get("message")
                why = msg if msg else reason_for(find_source(module, cn), cn, tc.get("name"))
                key = "%s %s" % (module, cn.split(".")[-1])
                skips.setdefault(why, {}).setdefault(key, []).append(tc.get("name"))
    passed = mod["tests"] - mod["skipped"] - mod["failures"] - mod["errors"]
    results.append("RESULTS %s: %d tests, %d passed, %d failed, %d errors, %d skipped"
                   % (module, mod["tests"], passed, mod["failures"], mod["errors"], mod["skipped"]))
    for k in total:
        total[k] += mod[k]

print("SKIPPED TESTS (%d), grouped by reason -> module TestClass: methods" % total["skipped"])
for why in sorted(skips, key=lambda w: -sum(len(v) for v in skips[w].values())):
    n = sum(len(v) for v in skips[why].values())
    print("SKIP REASON [%d test(s)]: %s" % (n, why))
    for key in sorted(skips[why]):
        print("    %s: %s" % (key, ", ".join(skips[why][key])))
for line in results:
    print(line)
passed = total["tests"] - total["skipped"] - total["failures"] - total["errors"]
print("RESULTS TOTAL: %d tests, %d passed, %d failed, %d errors, %d skipped"
      % (total["tests"], passed, total["failures"], total["errors"], total["skipped"]))
