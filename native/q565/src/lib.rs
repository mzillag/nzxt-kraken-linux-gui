//! Minimal C ABI around the `q565` crate (the QOI-style RGB565 codec that the
//! Kraken Elite firmware decodes in its "fast memory" image mode 0x08).
//!
//! The Python side loads this as a shared library with ctypes.  Keeping the
//! surface tiny avoids a pyo3 build dependency on Python headers.

use q565::encode::Q565EncodeContext;

/// Convert 8-bit RGB to RGB565 using the rounding AIOLCDUnchained uses
/// (it was verified against the colours CAM produces on the panel).
#[inline]
fn rgb888_to_565(r: u8, g: u8, b: u8) -> u16 {
    let r5 = ((r as u32 * 249 + 1014) >> 11) as u16;
    let g6 = ((g as u32 * 253 + 505) >> 10) as u16;
    let b5 = ((b as u32 * 249 + 1014) >> 11) as u16;
    (r5 << 11) | (g6 << 5) | b5
}

/// Encode a tightly packed RGB888 buffer of `width * height` pixels.
///
/// Returns the number of bytes written into `out`, or 0 on error (bad
/// arguments or output buffer too small).  `out_cap` should be at least
/// `8 + width * height * 3 + 1` to be safe for any input.
#[no_mangle]
pub extern "C" fn kraken_q565_encode_rgb888(
    rgb: *const u8,
    width: u32,
    height: u32,
    out: *mut u8,
    out_cap: usize,
) -> usize {
    if rgb.is_null() || out.is_null() || width == 0 || height == 0 {
        return 0;
    }
    let pixel_count = (width as usize) * (height as usize);
    let input = unsafe { std::slice::from_raw_parts(rgb, pixel_count * 3) };

    let mut rgb565: Vec<u16> = Vec::with_capacity(pixel_count);
    for px in input.chunks_exact(3) {
        rgb565.push(rgb888_to_565(px[0], px[1], px[2]));
    }

    let mut encoded: Vec<u8> = Vec::with_capacity(pixel_count * 3 / 2);
    if !Q565EncodeContext::encode_to_vec(width as u16, height as u16, &rgb565, &mut encoded) {
        return 0;
    }
    if encoded.len() > out_cap {
        return 0;
    }
    let output = unsafe { std::slice::from_raw_parts_mut(out, encoded.len()) };
    output.copy_from_slice(&encoded);
    encoded.len()
}

/// Library version, so Python can log which build it loaded.
#[no_mangle]
pub extern "C" fn kraken_q565_version() -> u32 {
    1
}
