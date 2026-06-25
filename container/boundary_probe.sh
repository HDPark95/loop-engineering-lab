#!/bin/sh
# Runs INSIDE the agent container. ADVERSARIAL: the agent tries every way to
# reach the oracle. Every line must show failure for the isolation to hold.
# Output is captured by the runner as a measured property (not an assumption).
echo "# adversarial boundary probe (run from inside agent container)"
echo "## 1. read the oracle store directly"
( cat /oracle/oracle.db 2>&1 | head -c 80 ) ; ls /oracle 2>&1 | head -1
echo "## 2. resolve the oracle host"
getent hosts oracle 2>&1 || echo "RESOLVE_FAIL (oracle not on agent network)"
echo "## 3. http to the oracle service"
curl -s --max-time 4 http://oracle:8000/ 2>&1 | head -1 ; [ $? -ne 0 ] && echo "CURL_FAIL"
echo "## 4. ping the oracle"
( ping -c1 -W2 oracle 2>&1 | tail -1 ) || echo "PING_FAIL"
echo "## 5. what networks/routes can the agent see"
ip -o addr show 2>/dev/null | awk '{print $2, $4}' ; ip route 2>/dev/null | head -3
echo "# end probe"
