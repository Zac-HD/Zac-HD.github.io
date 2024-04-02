async function _setup() {
  let params = new URLSearchParams(window.location.search);
  if (params.has("q")) {document.getElementById("function_names").setAttribute('value', params.get("q"))};
  if (params.has("writer") && params.get("writer") != "magic") {
    let writer = params.get("writer");
    document.querySelector(`input[value="${writer}"]`).checked = true;
  }
  if (params.has("style") && params.get("style") != "pytest") {
    let style = params.get("style");
    document.querySelector(`input[value="${style}"]`).checked = true;
  }

  document.querySelectorAll("[disabled]").forEach(function (btn) {
    btn.removeAttribute("disabled");
  });
  document.getElementById("ghostwriter-spinner").style.visibility = "hidden";
  document.getElementById("pytest-spinner").style.visibility = "hidden";
  if (params.has("q")) {write_tests();};
  if (params.has("code")) {
    code_div = document.getElementById("ghostwriter-output");
    code_div.textContent = base64Decode(params.get("code"));
    Prism.highlightElement(code_div);
    run_tests();
  };
}
window.addEventListener('DOMContentLoaded', _setup);

_pyodideLoadedPromise = new Promise(resolve => {
  window.__pyodideLoadedCallback = resolve;
});

const allow_time_for_paint = () => new Promise(resolve => setTimeout(resolve, 50));

async function _setup_pyodide() {
  let pyodide = await loadPyodide();
  // pyodide.setDebug(true)
  await pyodide.loadPackage("micropip");
  const micropip = pyodide.pyimport("micropip");
  await Promise.all([
    micropip.install('pytest'),
    micropip.install('hypothesis[cli]'),
    fetch("hydemo.py").then(res => res.text()).then((data) => {
      pyodide.FS.writeFile("hydemo.py", data, { encoding: "utf8" });
    })
  ]);
  window.hydemoMod = pyodide.pyimport("hydemo");
  window.__pyodideLoadedCallback();
}
_setup_pyodide();  // start this immediately in the background

const encoder = new TextEncoder();

async function _cached_call(fn_name, ...args) {
  raw_key = [fn_name, ...args].toString();
  const hashBuffer = await crypto.subtle.digest('SHA-256', encoder.encode(raw_key));
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  key = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
	const cached = localStorage.getItem(key)
	if (cached) {
    console.log("cache hit for", raw_key.substring(0, 50));
    return cached;
  };
  console.log("cache miss for", raw_key.substring(0, 50));
  await _pyodideLoadedPromise;
  try {
    result = await hydemoMod[fn_name](...args);
  } catch (err) {
    result = err;
  }
  localStorage.setItem(key, result);
  return result;
}

function set_query_params(func_name, writer, style, source_code) {
  let searchParams = new URLSearchParams();
  if (source_code === null) {
    searchParams.set("q", func_name);
    if (writer != "magic") { searchParams.set("writer", writer) };
    if (style != "pytest") { searchParams.set("style", style) };
  } else {
    searchParams.set("code", base64Encode(source_code));
  };
  const url = window.location.protocol + "//" + window.location.host + window.location.pathname + "?" + searchParams.toString();
  window.history.replaceState({path: url}, "", url)
}

GHOSTWRITTEN_TESTS = {};
GHOSTWRITER_PREFIX = "# This test code was written by the `hypothesis.extra.ghostwriter` module\n# and is provided under the Creative Commons Zero public domain dedication."

async function write_tests() {
  spinner = document.getElementById("ghostwriter-spinner");
  spinner.style.visibility = "visible";
  code_div = document.getElementById("ghostwriter-output");
  func_name = document.getElementById("function_names").value;
  writer = document.querySelector("input[name=writer]:checked").value;
  style = document.querySelector("input[name=style]:checked").value;

  set_query_params(func_name, writer, style, null);
  code_div.textContent = await _cached_call("write_a_test", func_name, writer, style);
  GHOSTWRITTEN_TESTS[func_name + ":" + writer + ":" + style] = code_div.textContent;
  Prism.highlightElement(code_div);
  spinner.style.visibility = "hidden";
  await allow_time_for_paint()
  await run_tests();
}

async function run_tests() {
  spinner = document.getElementById("pytest-spinner");
  spinner.style.visibility = "visible";
  test_div = document.getElementById("pytest-output");
  test_div.textContent = "results pending..."
  await allow_time_for_paint()
  code_div = document.getElementById("ghostwriter-output");
  source_code = await _cached_call("format_code", code_div.textContent);

  func_name = document.getElementById("function_names").value;
  writer = document.querySelector("input[name=writer]:checked").value;
  style = document.querySelector("input[name=style]:checked").value;
  prev_source = GHOSTWRITTEN_TESTS[func_name + ":" + writer + ":" + style];
  if (source_code === prev_source) {
    set_query_params(func_name, writer, style, null);
  } else {
    if (source_code.startsWith(GHOSTWRITER_PREFIX)) {
      source_code = source_code.slice(GHOSTWRITER_PREFIX.length).trim();
    }
    set_query_params(func_name, writer, style, source_code);
  };

  code_div.textContent = source_code;
  Prism.highlightElement(code_div);
  test_div.textContent = await _cached_call("run_tests", source_code);
  spinner.style.visibility = "hidden";
  Prism.highlightElement(test_div);
}

function base64Encode(str) {
  const buffer = new TextEncoder().encode(str);
  const base64Str = btoa(String.fromCharCode.apply(null, new Uint8Array(buffer)));
  return base64Str;
}

function base64Decode(base64Str) {
  const binaryStr = atob(base64Str);
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) {
    bytes[i] = binaryStr.charCodeAt(i);
  }
  const decodedStr = new TextDecoder().decode(bytes);
  return decodedStr;
}
