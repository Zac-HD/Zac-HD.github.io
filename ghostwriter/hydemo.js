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

async function write_tests() {
  spinner = document.getElementById("ghostwriter-spinner");
  spinner.style.visibility = "visible";
  code_div = document.getElementById("ghostwriter-output");
  func_name = document.getElementById("function_names").value;
  writer = document.querySelector("input[name=writer]:checked").value;
  style = document.querySelector("input[name=style]:checked").value;

  let searchParams = new URLSearchParams();
  searchParams.set("q", func_name);
  if (writer != "magic") { searchParams.set("writer", writer) };
  if (style != "pytest") { searchParams.set("style", style) };
  const url = window.location.protocol + "//" + window.location.host + window.location.pathname + "?" + searchParams.toString();
  window.history.replaceState({path: url}, "", url)
  code_div.textContent = await _cached_call("write_a_test", func_name, writer, style);
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
  code_div.textContent = source_code;
  Prism.highlightElement(code_div);
  test_div.textContent = await _cached_call("run_tests", source_code);
  spinner.style.visibility = "hidden";
  Prism.highlightElement(test_div);
}
