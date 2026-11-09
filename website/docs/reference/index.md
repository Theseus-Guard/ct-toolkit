# Reference

Complete API reference for CT Toolkit.

<div class="grid cards" markdown>
<ul>
  <li>
    <p><strong>TheseusWrapper</strong></p>
    <p>The main entry point. Wraps any LLM client with identity protection.</p>
    <p><a href="theseus-wrapper/">TheseusWrapper</a></p>
  </li>
  <li>
    <p><strong>WrapperConfig</strong></p>
    <p>All configuration options with types, defaults, and examples.</p>
    <p><a href="wrapper-config/">WrapperConfig</a></p>
  </li>
  <li>
    <p><strong>Policy &amp; Capability Metadata</strong></p>
    <p>Role-based policy resolution, capability registry, and standardized metadata surfaces.</p>
    <p><a href="policy-capabilities/">Policy &amp; Capability Metadata</a></p>
  </li>
  <li>
    <p><strong>ConstitutionalKernel</strong></p>
    <p>Loading, merging, validating, and updating kernels programmatically.</p>
    <p><a href="constitutional-kernel/">ConstitutionalKernel</a></p>
  </li>
  <li>
    <p><strong>Divergence Engine</strong></p>
    <p><code>DivergenceEngine</code>, <code>DivergenceResult</code>, <code>DivergenceTier</code>, <code>ElasticityScheduler</code>.</p>
    <p><a href="divergence-engine/">Divergence Engine</a></p>
  </li>
  <li>
    <p><strong>Context Compression Guard</strong></p>
    <p>Manages context window usage and applies compression strategies.</p>
    <p><a href="compression-guard/">Context Compression Guard</a></p>
  </li>
  <li>
    <p><strong>Provenance Log</strong></p>
    <p>Recording, verifying, exporting, and rolling back the HMAC hash chain.</p>
    <p><a href="provenance-log/">ProvenanceLog</a></p>
  </li>
  <li>
    <p><strong>Compatibility Matrix</strong></p>
    <p>Which template + kernel combinations are Native, Compatible, or Conflicting.</p>
    <p><a href="compatibility/">Compatibility Matrix</a></p>
  </li>
  <li>
    <p><strong>Kernels &amp; Templates</strong></p>
    <p>YAML specification for creating custom kernels and identity templates.</p>
    <p><a href="kernels-templates/">Kernels &amp; Templates</a></p>
  </li>
  <li>
    <p><strong>Circuit Breaker</strong></p>
    <p>Prevents cascading failures when LLM providers are down or rate-limited.</p>
    <p><a href="circuit-breaker/">Circuit Breaker</a></p>
  </li>
  <li>
    <p><strong>Metrics & Monitoring</strong></p>
    <p>Thread-safe metrics collection for monitoring and observability.</p>
    <p><a href="metrics/">Metrics</a></p>
  </li>
  <li>
    <p><strong>AsyncTheseusWrapper</strong></p>
    <p>Async-compatible TheseusWrapper with circuit breaker integration.</p>
    <p><a href="async-wrapper/">AsyncTheseusWrapper</a></p>
  </li>
  <li>
    <p><strong>Tool-Call Guard</strong></p>
    <p>Reviews the tool calls a model asks for: observe and enforce modes, the optional judge, and the audit trail.</p>
    <p><a href="../concepts/tool-call-guard/">Tool-Call Guard</a></p>
  </li>
  <li>
    <p><strong>CLI Reference</strong></p>
    <p>The <code>ct-toolkit</code> commands: status, audit, serve, proxy, tool-guard, scrub and kernel tools.</p>
    <p><a href="cli/">CLI Reference</a></p>
  </li>
  <li>
    <p><strong>Exceptions</strong></p>
    <p>Full exception hierarchy with usage examples.</p>
    <p><a href="exceptions/">Exceptions</a></p>
  </li>
</ul>

</div>
