//! `tools::find_in` against Python's answers in `tests/data/tools.json` (`record/tools_cases.py`): the skills, plugins
//! and servers each agent kind has installed, read from a home folder and project rebuilt in a scratch folder.
#![cfg(unix)]
use serde_json::Value;

#[test]
fn what_is_installed_reads_as_python_reads_it() {
    let data: Value = serde_json::from_str(include_str!("data/tools.json")).unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let root = scratch.path().canonicalize().unwrap();
    let root_text = root.to_str().unwrap();
    for (rel, body) in data["files"].as_object().unwrap() {
        let path = root.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let text = body.as_str().map_or_else(|| body.to_string(), String::from);
        std::fs::write(path, text.replace("@ROOT@", root_text)).unwrap();
    }
    for case in data["cases"].as_array().unwrap() {
        let (kind, own) = (case["kind"].as_str().unwrap(), case["own_copy"].as_bool().unwrap());
        let got = assieme_brain::tools::find_in(kind, root.join("proj").to_str(), &root.join("home"), own);
        let want = case["expect"].to_string().replace("@ROOT@", root_text);
        assert_eq!(got, serde_json::from_str::<Value>(&want).unwrap(), "{kind} own_copy={own}");
    }
}
