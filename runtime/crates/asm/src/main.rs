//! The shipped `asm` program: `asm brain …` runs the brain, anything else the core.
fn main() {
    let mut args = std::env::args_os().skip(1);
    if args.next().is_some_and(|a| a == "brain") {
        std::process::exit(brain(args.collect()));
    }
    std::process::exit(assieme_core::cli::main());
}

#[cfg(unix)]
fn brain(args: Vec<std::ffi::OsString>) -> i32 {
    assieme_brain::run(args)
}

#[cfg(not(unix))]
fn brain(_args: Vec<std::ffi::OsString>) -> i32 {
    eprintln!("The brain runs on macOS and Linux only.");
    1
}
