// Changed by Assieme: renamed and adapted from herdr-core v0.1.19; see NOTICE.
#[test]
fn a_restart_restores_settings_without_reusing_paths_or_lifecycle_marks() {
    let pairs =
        |pairs: &[(&str, &str)]| pairs.iter().map(|(key, value)| ((*key).into(), (*value).into())).collect::<Vec<_>>();
    let saved = pairs(&[
        ("PATH", "saved-path"),
        ("ASM_VALUE", "saved"),
        ("HOME", "old-home"),
        ("ASM_SESSION", "old-session"),
        ("ASM_SCOPE", "old-sweep"),
        ("ASM_RUNTIME", "old-runtime"),
        ("ASM_LAUNCHD_DIRECT", "old-bypass"),
        ("INVOCATION_ID", "old-service"),
        ("ASM_SOCKET", "old-socket"),
    ]);
    let current = pairs(&[
        ("PATH", "caller-path"),
        ("ASM_CALLER_ONLY", "must-not-leak"),
        ("HOME", "new-home"),
        ("ASM_SESSION", "new-session"),
        ("ASM_SCOPE", "caller-sweep"),
        ("ASM_LAUNCHD_DETACH", "caller-bypass"),
        ("INVOCATION_ID", "new-service"),
        ("ASM_SOCKET", "new-socket"),
    ]);
    let env = super::restored_environment(saved, current);
    for (key, value) in [
        ("PATH", "saved-path"),
        ("ASM_VALUE", "saved"),
        ("HOME", "new-home"),
        ("ASM_SESSION", "new-session"),
        ("ASM_SOCKET", "new-socket"),
        ("ASM_RUNTIME", "1"),
    ] {
        assert_eq!(env.get(std::ffi::OsStr::new(key)), Some(&std::ffi::OsString::from(value)));
    }
    for key in ["ASM_CALLER_ONLY", "ASM_SCOPE", "ASM_LAUNCHD_DIRECT", "ASM_LAUNCHD_DETACH"] {
        assert!(!env.contains_key(std::ffi::OsStr::new(key)), "{key}");
    }
    #[cfg(target_os = "linux")]
    assert_eq!(env.get(std::ffi::OsStr::new("INVOCATION_ID")), Some(&std::ffi::OsString::from("new-service")));
}

#[cfg(windows)]
#[test]
fn windows_path_keys_are_matched_without_case() {
    let env = super::restored_environment(
        [("home".into(), "old".into()), ("asm_scope".into(), "old".into()), ("Path".into(), "saved".into())],
        [("HOME".into(), "new".into()), ("Path".into(), "caller".into())],
    );
    assert!(!env.contains_key(std::ffi::OsStr::new("home")));
    assert!(!env.contains_key(std::ffi::OsStr::new("asm_scope")));
    assert_eq!(env.get(std::ffi::OsStr::new("HOME")), Some(&std::ffi::OsString::from("new")));
    assert_eq!(env.get(std::ffi::OsStr::new("Path")), Some(&std::ffi::OsString::from("saved")));
}
