async function _setup() {
  document.addEventListener('DOMContentLoaded', (event) => {
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
  })
  const hydemoPromise = fetch("hydemo.zip").then((resp) => resp.arrayBuffer());
  let pyodide = await loadPyodide();
  let micropip_promise = pyodide.loadPackage("micropip");
  const hydemoBuffer = await hydemoPromise;
  await micropip_promise;
  await pyodide.runPythonAsync(`
    import micropip
    await micropip.install('hypothesis[cli]')
`);
  pyodide.unpackArchive(hydemoBuffer, "zip");
  const hydemoMod = pyodide.pyimport("hydemo");
  console.log(hydemoMod);
  return hydemoMod;
}
_setup().then((hydemoMod) => {
  window.hydemoMod = hydemoMod;
  console.log(hydemoMod);
  document.querySelectorAll("[disabled]").forEach(function (btn) {
    btn.removeAttribute("disabled");
  });
  document.getElementById("ghostwriter-spinner").style.visibility = "hidden";
  document.getElementById("pytest-spinner").style.visibility = "hidden";
  let params = new URLSearchParams(window.location.search);
  if (params.has("q")) {write_tests();};
});

const allow_time_for_paint = () => new Promise(resolve => setTimeout(resolve, 50));

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

  code_div.textContent = await hydemoMod.write_a_test(func_name, writer, style);
  spinner.style.visibility = "hidden";
  await allow_time_for_paint()
  run_tests();
}

async function run_tests() {
  spinner = document.getElementById("pytest-spinner");
  spinner.style.visibility = "visible";
  test_div = document.getElementById("pytest-output");
  test_div.textContent = "results pending..."
  await allow_time_for_paint()
  code_div = document.getElementById("ghostwriter-output");
  source_code = hydemoMod.format_code(code_div.textContent);
  code_div.textContent = source_code;
  try {
    test_div.textContent = hydemoMod.run_tests(source_code);
  } catch (err) {
    test_div.textContent = err;
  }
  spinner.style.visibility = "hidden";
  Prism.highlightElement(test_div);
}
