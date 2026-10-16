# SPDX-License-Identifier: Apache-2.0
# Archive codec derived from AntonioDePau/KHPCPatchManager, OpenKH/egsEncryption.cs
# and OpenKH/egsHDAsset.cs. See LICENSE and NOTICE.
import functools
import zlib

MASTER_KEY = bytes.fromhex("7e8897550b06f108ebbb141cd87aec4134b2a346ef6bfee1cf53a50512d28e524a80e981b0f0b49cff0f1513da734e77bed730e5f65a113767bc836f2776d0cd690d2e514290b8b64cadce5b1a1ff5af01f85e3a6e688be89fc9d9269229c833983254d4442566ac5f9921e48f1dc2d5a462f90261de59e7079afa2f953f86d378a775edd62d6487bdc7c1aaf28c17cb318ac3cc04ee6aab5c2270ca9e716d85455db9a9a61047fb827d847bc6e238fc2b0e209dc5f339a8a06558437ce33618724979aed17440c4914f2463bfba239650b357df1e03487f354d3ee6a1dd093c3d3b568d932a9b4b0c28b1e0608919db2cf76cb51b94c0dceab70af416fda200")
ROUND_CONSTANTS = (1, 2, 4, 8, 16, 32, 64, 128, 27, 54)


@functools.lru_cache(maxsize=1024)
def xor_mask(seed):
    if len(seed) != 16:
        raise ValueError("Invalid asset seed size")
    key = bytearray(176)
    key[:16] = bytes(value if value else index for index, value in enumerate(seed))
    for index in range(40):
        frame = key[12 + index * 4:16 + index * 4]
        if index % 4 == 0:
            frame = bytes((MASTER_KEY[frame[1]] ^ ROUND_CONSTANTS[index // 4],
                           MASTER_KEY[frame[2]], MASTER_KEY[frame[3]], MASTER_KEY[frame[0]]))
        for column in range(4):
            key[16 + index * 4 + column] = key[index * 4 + column] ^ frame[column]
    mask = bytearray(16)
    for round_index in range(11):
        for column in range(16):
            mask[column] ^= key[round_index * 16 + column]
    return bytes(mask)


def decode_data(data, seed, packed_size, expected_size):
    raw = bytearray(data)
    if packed_size > -2:
        mask = xor_mask(seed)
        for index in range(min(len(raw), 256)):
            raw[index] ^= mask[index % 16]
    if packed_size >= 0:
        # KHPCPatchManager skips the two-byte zlib header and reads raw deflate.
        decoder = zlib.decompressobj(-15)
        plain = decoder.decompress(bytes(raw[2:packed_size]), expected_size + 1)
        if not decoder.eof or len(plain) != expected_size:
            raise ValueError("Compressed audio length does not match its header")
    else:
        plain = bytes(raw[:expected_size])
        if len(plain) != expected_size:
            raise ValueError("Truncated uncompressed audio")
    return plain


def read_audio(stream, header, seed):
    stream.seek(header["physical_offset"])
    raw = stream.read(header["stored_size"])
    if len(raw) != header["stored_size"]:
        raise ValueError("Truncated stored audio")
    return decode_data(raw, seed, header["packed_size"], header["size"])


def encode_data(plain, seed, original_packed_size):
    """Retain the source entry's compression mode and its archive cipher."""
    raw = zlib.compress(plain, 6) if original_packed_size >= 0 else plain
    raw = bytearray(raw + b"\xcd" * (-len(raw) % 16))
    if original_packed_size > -2:
        mask = xor_mask(seed)
        for index in range(min(len(raw), 256)):
            raw[index] ^= mask[index % 16]
    packed_size = len(raw) if original_packed_size >= 0 else original_packed_size
    return bytes(raw), packed_size
