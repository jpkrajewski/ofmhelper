# Copyright (C) 2026 jpkrajewski

import io
import math
from fractions import Fraction

from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

lazy from ofmhelpers.media.recorder.models import Device
lazy from ofmhelpers.media.repurpose.photos.models import Lens, Shot

_B = ExifTags.Base


def _q(value: float) -> IFDRational:
    return IFDRational(Fraction(value).limit_denominator(1_000_000))


def _put_dms(ifd: dict[int, object], tag: int, value: float) -> None:
    value = abs(value)
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = ((value - degrees) * 60 - minutes) * 60
    ifd[tag] = (_q(degrees), _q(minutes), _q(round(seconds, 4)))


def _without_jfif(jpeg: bytes) -> bytes:
    if jpeg[2:4] != b"\xff\xe0":
        return jpeg
    return jpeg[:2] + jpeg[4 + int.from_bytes(jpeg[4:6], "big") :]


def exif_for(
    device: Device,
    lens: Lens,
    shot: Shot,
    size: tuple[int, int],
) -> Image.Exif:
    when = shot.taken.strftime("%Y:%m:%d %H:%M:%S")
    offset = shot.taken.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}"
    sub_s = f"{shot.taken.microsecond // 1000:03d}"
    shutter = math.log2(1 / shot.exposure)
    aperture = 2 * math.log2(lens.f_number)
    brightness = aperture + shutter - math.log2(shot.iso / 3.125)

    exif = Image.Exif()
    exif[_B.Make] = device.make
    exif[_B.Model] = device.model
    exif[_B.Orientation] = 1
    exif[_B.XResolution] = _q(72)
    exif[_B.YResolution] = _q(72)
    exif[_B.ResolutionUnit] = 2
    exif[_B.Software] = device.software
    exif[_B.DateTime] = when
    exif[_B.HostComputer] = device.model
    exif[_B.YCbCrPositioning] = 1

    tags = exif.get_ifd(ExifTags.IFD.Exif)
    tags[_B.ExposureTime] = IFDRational(
        shot.exposure.numerator,
        shot.exposure.denominator,
    )
    tags[_B.FNumber] = _q(lens.f_number)
    tags[_B.ExposureProgram] = 2
    tags[_B.ISOSpeedRatings] = shot.iso
    tags[_B.ExifVersion] = b"0232"
    tags[_B.ComponentsConfiguration] = b"\x01\x02\x03\x00"
    tags[_B.DateTimeOriginal] = when
    tags[_B.DateTimeDigitized] = when
    tags[_B.OffsetTime] = offset
    tags[_B.OffsetTimeOriginal] = offset
    tags[_B.OffsetTimeDigitized] = offset
    tags[_B.ShutterSpeedValue] = _q(shutter)
    tags[_B.ApertureValue] = _q(aperture)
    tags[_B.BrightnessValue] = _q(brightness)
    tags[_B.ExposureBiasValue] = _q(0)
    tags[_B.MeteringMode] = 5
    tags[_B.Flash] = 16
    tags[_B.FocalLength] = _q(lens.focal_length_mm)
    tags[_B.SubsecTimeOriginal] = sub_s
    tags[_B.SubsecTimeDigitized] = sub_s
    tags[_B.ColorSpace] = 1
    tags[_B.ExifImageWidth] = size[0]
    tags[_B.ExifImageHeight] = size[1]
    tags[_B.SensingMethod] = 2
    tags[_B.SceneType] = b"\x01"
    tags[_B.ExposureMode] = 0
    tags[_B.WhiteBalance] = 0
    tags[_B.FocalLengthIn35mmFilm] = lens.focal_length_35mm
    tags[_B.SceneCaptureType] = 0
    tags[_B.LensSpecification] = (
        _q(lens.focal_length_mm),
        _q(lens.focal_length_mm),
        _q(lens.f_number),
        _q(lens.f_number),
    )
    tags[_B.LensMake] = lens.make
    tags[_B.LensModel] = lens.model
    tags[_B.CompositeImage] = 2

    if shot.place is not None:
        point = shot.place.point
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps[ExifTags.GPS.GPSVersionID] = b"\x02\x02\x00\x00"
        gps[ExifTags.GPS.GPSLatitudeRef] = "N" if point.latitude >= 0 else "S"
        _put_dms(gps, ExifTags.GPS.GPSLatitude, point.latitude)
        gps[ExifTags.GPS.GPSLongitudeRef] = "E" if point.longitude >= 0 else "W"
        _put_dms(gps, ExifTags.GPS.GPSLongitude, point.longitude)
        gps[ExifTags.GPS.GPSAltitudeRef] = b"\x00"
        gps[ExifTags.GPS.GPSAltitude] = _q(round(max(point.altitude_m, 0.0), 3))
        gps[ExifTags.GPS.GPSHPositioningError] = _q(shot.place.accuracy_m)
    return exif


def jpeg(picture: Image.Image, exif: Image.Exif, quality: int) -> bytes:
    picture.info.clear()
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=quality, exif=exif)
    return _without_jfif(out.getvalue())
