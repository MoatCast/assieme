//! The shipped `asm` program: `asm brain …` runs the brain, `asm hook …`, `asm askhook`, `asm mcp` and `asm cli …`
//! are the brain's other commands, anything else is the core's. The hooks run before anything else is set up: they
//! must start in under 20 ms.
fn main() {
    if let Some(code) = brain_commands(std::env::args_os().skip(1).collect()) {
        std::process::exit(code);
    }
    std::process::exit(assieme_core::cli::main());
}

#[cfg(unix)]
fn brain_commands(args: Vec<std::ffi::OsString>) -> Option<i32> {
    let words =
        |from: usize| -> Vec<String> { args[from..].iter().map(|a| a.to_string_lossy().into_owned()).collect() };
    Some(match args.first()?.to_str()? {
        "brain" => assieme_brain::run(args[1..].to_vec()),
        "hook" => assieme_brain::hook::main(&words(1)),
        "askhook" => assieme_brain::askhook::main(),
        "mcp" => assieme_brain::mcpserver::main(),
        "cli" => assieme_brain::cli::main(words(1)),
        _ => return None,
    })
}

#[cfg(not(unix))]
fn brain_commands(args: Vec<std::ffi::OsString>) -> Option<i32> {
    args.first().is_some_and(|a| a == "brain").then(|| {
        eprintln!("The brain runs on macOS and Linux only.");
        1
    })
}
