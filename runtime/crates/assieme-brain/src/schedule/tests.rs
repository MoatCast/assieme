use super::*;

/// Python's answers for every time case: the same slots, words and refusals.
#[test]
fn matches_the_recorded_python_answers() {
    let path = format!("{}/tests/data/schedule.json", env!("CARGO_MANIFEST_DIR"));
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let ts = |v: &Value| v.as_str().unwrap().parse::<Timestamp>().unwrap();
    for c in data["slots"].as_array().unwrap() {
        let tz = TimeZone::get(text(c, "zone")).unwrap();
        let (created, now) = (ts(&c["created"]), ts(&c["now"]));
        assert_eq!(latest(&c["every"], created, now, &tz).map(iso).as_deref(), c["latest"].as_str(), "{c}");
        assert_eq!(iso(slot(&c["every"], created, now, &tz).1), text(c, "next"), "{c}");
    }
    for c in data["words"].as_array().unwrap() {
        assert_eq!(when_words(&c["every"]), text(c, "when"), "{c}");
    }
    for c in data["days"].as_array().unwrap() {
        assert_eq!(
            day_words(ts(&c["time"]), ts(&c["now"]), &TimeZone::get(text(c, "zone")).unwrap()),
            text(c, "words"),
            "{c}"
        );
    }
    for c in data["checks"].as_array().unwrap() {
        match (parse_every(&c["raw"]), &c["ok"], &c["error"]) {
            (Ok(got), want, Value::Null) => assert_eq!(&got, want, "{c}"),
            (Err(got), Value::Null, want) => assert_eq!(&got.0, want.as_str().unwrap(), "{c}"),
            (got, ..) => panic!("{c}: {got:?}"),
        }
    }
}
