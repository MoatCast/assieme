//! herdr-core's framed mode: `[channel u32 LE][kind u8][len u32 LE][payload]`. Channel 0 is JSON-RPC.
use std::io::{self, Read};

pub const DATA: u8 = 0;
pub const CREDIT: u8 = 1;
pub const CLOSE: u8 = 2;
pub const RESIZE: u8 = 3;
const MAX_PAYLOAD: usize = 16 << 20;

pub fn encode(channel: u32, kind: u8, payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(9 + payload.len());
    out.extend(channel.to_le_bytes());
    out.push(kind);
    out.extend((payload.len() as u32).to_le_bytes());
    out.extend(payload);
    out
}

/// One frame, or `Ok(None)` on a clean end of stream between frames.
pub fn read(r: &mut impl Read) -> io::Result<Option<(u32, u8, Vec<u8>)>> {
    let mut h = [0u8; 9];
    match r.read_exact(&mut h) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e),
    }
    let channel = u32::from_le_bytes(h[0..4].try_into().unwrap());
    let len = u32::from_le_bytes(h[5..9].try_into().unwrap()) as usize;
    if len > MAX_PAYLOAD {
        return Err(io::Error::other(format!("frame of {len} bytes is too large")));
    }
    let mut payload = vec![0; len];
    r.read_exact(&mut payload)?;
    Ok(Some((channel, h[4], payload)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_header_layout() {
        assert_eq!(&encode(1, DATA, b"hi")[..9], &[1, 0, 0, 0, 0, 2, 0, 0, 0]);
        assert_eq!(&encode(1, DATA, b"hi")[9..], b"hi");
    }

    #[test]
    fn read_round_trip_and_clean_eof() {
        let mut bytes = encode(0, DATA, b"{}");
        bytes.extend(encode(7, CREDIT, &5u32.to_le_bytes()));
        let mut r = &bytes[..];
        assert_eq!(read(&mut r).unwrap(), Some((0, DATA, b"{}".to_vec())));
        assert_eq!(read(&mut r).unwrap(), Some((7, CREDIT, vec![5, 0, 0, 0])));
        assert_eq!(read(&mut r).unwrap(), None);
    }

    #[test]
    fn oversized_frame_is_an_error() {
        let mut h = vec![0, 0, 0, 0, DATA];
        h.extend(((MAX_PAYLOAD + 1) as u32).to_le_bytes());
        assert!(read(&mut &h[..]).is_err());
    }
}
