//! `Runtime::ensure_core` starting a core: `asm server start` with the allow-listed environment and quiet send's
//! folder first on PATH. Its own file: it sets the process environment.
#![cfg(unix)]
use assieme_brain::runtime::Runtime;

#[test]
fn a_missing_core_is_started_with_the_allow_listed_environment() {
    let dir = std::path::PathBuf::from(format!("/var/tmp/brain-rt-start-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("bin")).unwrap();
    let asm = dir.join("bin/asm");
    std::fs::write(&asm, format!("#!/bin/sh\necho \"$@\" > {0}/args\nenv > {0}/env\n", dir.display())).unwrap();
    std::fs::set_permissions(&asm, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    unsafe {
        std::env::set_var("PATH", format!("{}:/usr/bin:/bin", dir.join("bin").display()));
        std::env::set_var("XDG_STATE_HOME", dir.join("state"));
        std::env::set_var("CLAUDECODE", "1");
        std::env::set_var("SOME_TOKEN", "secret");
    }
    let sock = dir.join("state/assieme/asm.sock");
    std::fs::create_dir_all(sock.parent().unwrap()).unwrap();
    std::fs::write(&sock, "").unwrap(); // a stale socket file is removed first
    assert_eq!(Runtime::new("test", Some(sock.clone())).ensure_core(), Ok(()));
    assert!(!sock.exists());
    assert_eq!(std::fs::read_to_string(dir.join("args")).unwrap().trim(), "server start");
    let env = std::fs::read_to_string(dir.join("env")).unwrap();
    assert!(env.contains("ASM_SESSION=test\n"));
    assert!(env.contains(&format!("ASM_SOCKET={}\n", sock.display())));
    assert!(env.contains(&format!("PATH={}/state/assieme/bin:", dir.display())));
    assert!(!env.contains("CLAUDECODE") && !env.contains("SOME_TOKEN"), "{env}");
    let _ = std::fs::remove_dir_all(&dir);
}
