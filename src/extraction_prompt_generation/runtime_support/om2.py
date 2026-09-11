"""Stable OM-2 graph helpers shared by generated ontology packages.

Human-maintained. Flattened into generated script packages with the RDF
runtime. See runtime_support/README.md.

Unit aliases and qualitative presets cover common source spellings. They
map onto OM-2 individuals only; they do not encode an application ontology.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")

QUALITATIVE_TEMPERATURE_PRESETS: dict[str, str] = {
    "room temperature": "room temperature",
    "room temp": "room temperature",
    "room temperature conditions": "room temperature",
    "room temperature condition": "room temperature",
    "ordinary temperature": "room temperature",
    "ordinary temp": "room temperature",
    "lab temperature": "room temperature",
    "laboratory temperature": "room temperature",
    "ambient temperature": "room temperature",
    "ambient temp": "room temperature",
    "ambient conditions": "room temperature",
    "ambient": "room temperature",
    "amb": "room temperature",
    "rt": "room temperature",
    "r t": "room temperature",
    "rm temp": "room temperature",
    "ice bath": "ice bath",
    "ice water bath": "ice bath",
    "ice water": "ice bath",
    "ice cold": "ice bath",
    "ice cooling": "ice bath",
    "cooling ice bath": "ice bath",
    "dry ice": "dry ice",
    "dry ice bath": "dry ice",
    "acetone dry ice": "dry ice",
    "dry ice acetone": "dry ice",
    "liquid nitrogen": "liquid nitrogen",
    "liq n2": "liquid nitrogen",
    "ln2": "liquid nitrogen",
    "oil bath": "oil bath",
    "sand bath": "sand bath",
    "water bath": "water bath",
    "steam bath": "steam bath",
    "reflux": "reflux",
    "at reflux": "reflux",
    "under reflux": "reflux",
    "refluxing": "reflux",
    "gentle reflux": "reflux",
    "mild reflux": "reflux",
    "boiling": "reflux",
    "at boiling": "reflux",
    "boiling point": "reflux",
}
QUALITATIVE_DURATION_PRESETS: dict[str, str] = {
    "overnight": "overnight",
    "over night": "overnight",
    "o n": "overnight",
    "overnight period": "overnight",
    "weekend": "weekend",
    "brief": "brief",
    "briefly": "brief",
    "shortly": "brief",
    "several minutes": "brief",
    "a few minutes": "brief",
    "few minutes": "brief",
    "a few seconds": "brief",
    "few seconds": "brief",
    "several hours": "several hours",
    "a few hours": "several hours",
    "few hours": "several hours",
    "several days": "several days",
    "a few days": "several days",
    "few days": "several days",
    "several weeks": "several weeks",
    "period of several weeks": "several weeks",
    "prolonged": "prolonged",
    "prolonged time": "prolonged",
    "prolonged period": "prolonged",
}
QUALITATIVE_PRESSURE_PRESETS: dict[str, str] = {
    "vacuum": "vacuum",
    "under vacuum": "vacuum",
    "in vacuum": "vacuum",
    "in vacuo": "vacuum",
    "vacuo": "vacuum",
    "vac": "vacuum",
    "high vacuum": "vacuum",
    "static vacuum": "vacuum",
    "dynamic vacuum": "vacuum",
    "house vacuum": "vacuum",
    "reduced pressure": "reduced pressure",
    "under reduced pressure": "reduced pressure",
    "at reduced pressure": "reduced pressure",
    "aspirator": "reduced pressure",
    "rotary evaporation": "reduced pressure",
    "autogenous pressure": "autogenous pressure",
    "autogenous": "autogenous pressure",
    "self generated pressure": "autogenous pressure",
    "ambient pressure": "ambient pressure",
    "atmospheric pressure": "ambient pressure",
    "atmosphere": "ambient pressure",
    "open air": "ambient pressure",
    "open atmosphere": "ambient pressure",
}
NUMBER_WORD_VALUES: dict[str, float] = {
    "a": 1.0,
    "an": 1.0,
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
    "thirteen": 13.0,
    "fourteen": 14.0,
    "fifteen": 15.0,
    "sixteen": 16.0,
    "seventeen": 17.0,
    "eighteen": 18.0,
    "nineteen": 19.0,
    "twenty": 20.0,
    "thirty": 30.0,
    "forty": 40.0,
    "fifty": 50.0,
    "sixty": 60.0,
    "seventy": 70.0,
    "eighty": 80.0,
    "ninety": 90.0,
    "hundred": 100.0,
    "half": 0.5,
    "quarter": 0.25,
    "dozen": 12.0,
}

# Case-sensitive leading tokens. Chemistry uses capital M/N and mM; those
# collide with metre / millimetre / newton after casefold.
_CASE_UNIT_ALIASES: tuple[tuple[str, str], ...] = (
    ("mM", "mmolar"),
    ("µM", "umolar"),
    ("μM", "umolar"),
    ("uM", "umolar"),
    ("nM", "nmolar"),
    ("pM", "pmolar"),
    ("M", "molar"),
    ("N", "normal"),
)

OM2_UNIT_MAP: dict[str, URIRef] = {
    # Temperature
    "c": OM2.degreeCelsius,
    "°c": OM2.degreeCelsius,
    "degc": OM2.degreeCelsius,
    "deg c": OM2.degreeCelsius,
    "celsius": OM2.degreeCelsius,
    "centigrade": OM2.degreeCelsius,
    "degree celsius": OM2.degreeCelsius,
    "degrees celsius": OM2.degreeCelsius,
    "degree c": OM2.degreeCelsius,
    "degrees c": OM2.degreeCelsius,
    "deg": OM2.degreeCelsius,
    "degree": OM2.degreeCelsius,
    "degrees": OM2.degreeCelsius,
    "f": OM2.degreeFahrenheit,
    "°f": OM2.degreeFahrenheit,
    "degf": OM2.degreeFahrenheit,
    "fahrenheit": OM2.degreeFahrenheit,
    "degree fahrenheit": OM2.degreeFahrenheit,
    "degrees fahrenheit": OM2.degreeFahrenheit,
    "k": OM2.kelvin,
    "°k": OM2.kelvin,
    "kelvin": OM2.kelvin,
    "kelvins": OM2.kelvin,
    # Temperature rate
    "k/min": OM2.kelvinPerMinute,
    "k/h": OM2.kelvinPerHour,
    "k/s": OM2.kelvinPerSecond,
    "k min-1": OM2.kelvinPerMinute,
    "k h-1": OM2.kelvinPerHour,
    "k s-1": OM2.kelvinPerSecond,
    "c/min": OM2.degreeCelsiusPerMinute,
    "°c/min": OM2.degreeCelsiusPerMinute,
    "deg/min": OM2.degreeCelsiusPerMinute,
    "degc/min": OM2.degreeCelsiusPerMinute,
    "°c per min": OM2.degreeCelsiusPerMinute,
    "degree celsius per minute": OM2.degreeCelsiusPerMinute,
    "c/h": OM2.degreeCelsiusPerHour,
    "°c/h": OM2.degreeCelsiusPerHour,
    "deg/h": OM2.degreeCelsiusPerHour,
    "degc/h": OM2.degreeCelsiusPerHour,
    "°c per h": OM2.degreeCelsiusPerHour,
    "degree celsius per hour": OM2.degreeCelsiusPerHour,
    "c/s": OM2.degreeCelsiusPerSecond,
    "°c/s": OM2.degreeCelsiusPerSecond,
    "degc/s": OM2.degreeCelsiusPerSecond,
    "c s-1": OM2.degreeCelsiusPerSecond,
    "°c s-1": OM2.degreeCelsiusPerSecond,
    "c h-1": OM2.degreeCelsiusPerHour,
    "°c h-1": OM2.degreeCelsiusPerHour,
    "degc h-1": OM2.degreeCelsiusPerHour,
    "deg h-1": OM2.degreeCelsiusPerHour,
    "degc/h-1": OM2.degreeCelsiusPerHour,
    "cel/h": OM2.degreeCelsiusPerHour,
    "cel.h-1": OM2.degreeCelsiusPerHour,
    "cel h-1": OM2.degreeCelsiusPerHour,
    "c min-1": OM2.degreeCelsiusPerMinute,
    "°c min-1": OM2.degreeCelsiusPerMinute,
    "degc min-1": OM2.degreeCelsiusPerMinute,
    "deg min-1": OM2.degreeCelsiusPerMinute,
    "cel/min": OM2.degreeCelsiusPerMinute,
    "cel.min-1": OM2.degreeCelsiusPerMinute,
    # Duration
    "s": OM2.second,
    "sec": OM2.second,
    "secs": OM2.second,
    "second": OM2.second,
    "seconds": OM2.second,
    "ms": OM2.millisecond,
    "msec": OM2.millisecond,
    "msecs": OM2.millisecond,
    "millisecond": OM2.millisecond,
    "milliseconds": OM2.millisecond,
    "us": OM2.microsecond,
    "µs": OM2.microsecond,
    "μs": OM2.microsecond,
    "usec": OM2.microsecond,
    "microsecond": OM2.microsecond,
    "microseconds": OM2.microsecond,
    "ns": OM2.nanosecond,
    "nsec": OM2.nanosecond,
    "nanosecond": OM2.nanosecond,
    "nanoseconds": OM2.nanosecond,
    "ps": OM2.picosecond,
    "psec": OM2.picosecond,
    "picosecond": OM2.picosecond,
    "picoseconds": OM2.picosecond,
    "min": OM2.minute,
    "mins": OM2.minute,
    "minute": OM2.minute,
    "minutes": OM2.minute,
    "h": OM2.hour,
    "hr": OM2.hour,
    "hrs": OM2.hour,
    "hour": OM2.hour,
    "hours": OM2.hour,
    "d": OM2.day,
    "day": OM2.day,
    "days": OM2.day,
    "wk": OM2.week,
    "wks": OM2.week,
    "week": OM2.week,
    "weeks": OM2.week,
    "mo": OM2.month,
    "mos": OM2.month,
    "month": OM2.month,
    "months": OM2.month,
    "y": OM2.year,
    "yr": OM2.year,
    "yrs": OM2.year,
    "year": OM2.year,
    "years": OM2.year,
    # Pressure
    "pa": OM2.pascal,
    "kpa": OM2.kilopascal,
    "mpa": OM2.megapascal,
    "gpa": OM2.gigapascal,
    "hpa": OM2.hectopascal,
    "bar": OM2.bar,
    "bars": OM2.bar,
    "kbar": OM2.kilobar,
    "kbars": OM2.kilobar,
    "mbar": OM2.millibar,
    "mbars": OM2.millibar,
    "atm": OM2.standardAtmosphere,
    "atms": OM2.standardAtmosphere,
    "atmosphere": OM2.standardAtmosphere,
    "atmospheres": OM2.standardAtmosphere,
    "torr": OM2.torr,
    "mtorr": OM2.torr,
    "mmhg": OM2.millimetreOfMercury,
    "mm hg": OM2.millimetreOfMercury,
    "mm of hg": OM2.millimetreOfMercury,
    "mm of mercury": OM2.millimetreOfMercury,
    "millimetre of mercury": OM2.millimetreOfMercury,
    "millimeter of mercury": OM2.millimetreOfMercury,
    "inhg": OM2.millimetreOfMercury,
    "in hg": OM2.millimetreOfMercury,
    "psi": OM2.poundForcePerSquareInch,
    "psig": OM2.poundForcePerSquareInch,
    "psia": OM2.poundForcePerSquareInch,
    # Volume
    "l": OM2.litre,
    "liter": OM2.litre,
    "litre": OM2.litre,
    "liters": OM2.litre,
    "litres": OM2.litre,
    "ml": OM2.millilitre,
    "mls": OM2.millilitre,
    "milliliter": OM2.millilitre,
    "millilitre": OM2.millilitre,
    "milliliters": OM2.millilitre,
    "millilitres": OM2.millilitre,
    "cc": OM2.cubicCentimetre,
    "cm3": OM2.cubicCentimetre,
    "cm^3": OM2.cubicCentimetre,
    "cm³": OM2.cubicCentimetre,
    "dm3": OM2.litre,
    "dm^3": OM2.litre,
    "dm³": OM2.litre,
    "m3": OM2.cubicMetre,
    "m^3": OM2.cubicMetre,
    "m³": OM2.cubicMetre,
    "ul": OM2.microlitre,
    "uls": OM2.microlitre,
    "µl": OM2.microlitre,
    "μl": OM2.microlitre,
    "microliter": OM2.microlitre,
    "microlitre": OM2.microlitre,
    "nl": OM2.nanolitre,
    "nanoliter": OM2.nanolitre,
    "nanolitre": OM2.nanolitre,
    "pl": OM2.picolitre,
    "picoliter": OM2.picolitre,
    "picolitre": OM2.picolitre,
    # Amount fraction / percent spellings
    "%": OM2.percent,
    "percent": OM2.percent,
    "per cent": OM2.percent,
    "percentage": OM2.percent,
    "pct": OM2.percent,
    "mol%": OM2.percent,
    "mol %": OM2.percent,
    "mol.%": OM2.percent,
    "mole%": OM2.percent,
    "mole %": OM2.percent,
    "mol percent": OM2.percent,
    "mole percent": OM2.percent,
    "mol/mol": OM2.percent,
    "mol/mol%": OM2.percent,
    "wt%": OM2.percent,
    "wt %": OM2.percent,
    "wt.%": OM2.percent,
    "wt. %": OM2.percent,
    "weight%": OM2.percent,
    "weight %": OM2.percent,
    "weight percent": OM2.percent,
    "percent by weight": OM2.percent,
    "percent by mass": OM2.percent,
    "mass%": OM2.percent,
    "mass %": OM2.percent,
    "mass percent": OM2.percent,
    "vol%": OM2.percent,
    "vol %": OM2.percent,
    "vol.%": OM2.percent,
    "vol. %": OM2.percent,
    "volume%": OM2.percent,
    "volume %": OM2.percent,
    "volume percent": OM2.percent,
    "percent by volume": OM2.percent,
    "v/v%": OM2.percent,
    "v/v %": OM2.percent,
    "v/v": OM2.percent,
    "vol/vol": OM2.percent,
    "vol/vol%": OM2.percent,
    "w/w%": OM2.percent,
    "w/w %": OM2.percent,
    "w/w": OM2.percent,
    "wt/wt": OM2.percent,
    "wt/wt%": OM2.percent,
    "w/v%": OM2.percent,
    "w/v": OM2.percent,
    "wt/vol": OM2.percent,
    "wt/vol%": OM2.percent,
    "weight/volume": OM2.percent,
    "atom%": OM2.percent,
    "atom %": OM2.percent,
    "at%": OM2.percent,
    "at %": OM2.percent,
    "at.%": OM2.percent,
    "atomic %": OM2.percent,
    "atomic percent": OM2.percent,
    "area%": OM2.percent,
    "area %": OM2.percent,
    "area percent": OM2.percent,
    "‰": OM2.permille,
    "permille": OM2.permille,
    "per mille": OM2.permille,
    "ppt": OM2.partsPerTrillion,
    "ppm": OM2.partsPerMillion,
    "ppb": OM2.partsPerBillion,
    "ppth": OM2.permille,
    "parts per million": OM2.partsPerMillion,
    "parts per billion": OM2.partsPerBillion,
    "parts per trillion": OM2.partsPerTrillion,
    # Mass
    "kg": OM2.kilogram,
    "kgs": OM2.kilogram,
    "kilogram": OM2.kilogram,
    "kilograms": OM2.kilogram,
    "g": OM2.gram,
    "gm": OM2.gram,
    "gms": OM2.gram,
    "gram": OM2.gram,
    "grams": OM2.gram,
    "mg": OM2.milligram,
    "mgs": OM2.milligram,
    "milligram": OM2.milligram,
    "milligrams": OM2.milligram,
    "ug": OM2.microgram,
    "µg": OM2.microgram,
    "μg": OM2.microgram,
    "mcg": OM2.microgram,
    "microgram": OM2.microgram,
    "micrograms": OM2.microgram,
    "ng": OM2.nanogram,
    "nanogram": OM2.nanogram,
    "nanograms": OM2.nanogram,
    "pg": OM2.picogram,
    "picogram": OM2.picogram,
    "picograms": OM2.picogram,
    # Amount of substance
    "mol": OM2.mole,
    "mole": OM2.mole,
    "moles": OM2.mole,
    "mmol": OM2.millimole,
    "mmoles": OM2.millimole,
    "millimole": OM2.millimole,
    "millimoles": OM2.millimole,
    "umol": OM2.micromole,
    "µmol": OM2.micromole,
    "μmol": OM2.micromole,
    "micromole": OM2.micromole,
    "micromoles": OM2.micromole,
    "nmol": OM2.nanomole,
    "nanomole": OM2.nanomole,
    "nanomoles": OM2.nanomole,
    "pmol": OM2.picomole,
    "picomole": OM2.picomole,
    "picomoles": OM2.picomole,
    "eq": OM2.equivalent,
    "eqs": OM2.equivalent,
    "equiv": OM2.equivalent,
    "equivs": OM2.equivalent,
    "equivalent": OM2.equivalent,
    "equivalents": OM2.equivalent,
    "molar equiv": OM2.equivalent,
    "molar equivalent": OM2.equivalent,
    # Concentration (casefold keys; capital M/N handled separately)
    "molar": OM2.molePerLitre,
    "mol/l": OM2.molePerLitre,
    "mol/liter": OM2.molePerLitre,
    "mol/litre": OM2.molePerLitre,
    "mol l-1": OM2.molePerLitre,
    "mol/dm3": OM2.molePerLitre,
    "mol dm-3": OM2.molePerLitre,
    "mmolar": OM2.millimolePerLitre,
    "mmol/l": OM2.millimolePerLitre,
    "mmol l-1": OM2.millimolePerLitre,
    "umolar": OM2.micromolePerLitre,
    "umol/l": OM2.micromolePerLitre,
    "µmol/l": OM2.micromolePerLitre,
    "umol l-1": OM2.micromolePerLitre,
    "nmolar": OM2.nanomolePerLitre,
    "nmol/l": OM2.nanomolePerLitre,
    "pmolar": OM2.picomolePerLitre,
    "pmol/l": OM2.picomolePerLitre,
    "normal": OM2.normal,
    "g/l": OM2.gramPerLitre,
    "g l-1": OM2.gramPerLitre,
    "mg/l": OM2.milligramPerLitre,
    "mg l-1": OM2.milligramPerLitre,
    "mg/ml": OM2.milligramPerMillilitre,
    "mg ml-1": OM2.milligramPerMillilitre,
    "ug/ml": OM2.microgramPerMillilitre,
    "µg/ml": OM2.microgramPerMillilitre,
    "ug ml-1": OM2.microgramPerMillilitre,
    "ng/ml": OM2.nanogramPerMillilitre,
    "ng ml-1": OM2.nanogramPerMillilitre,
    "g/cm3": OM2.gramPerCubicCentimetre,
    "g cm-3": OM2.gramPerCubicCentimetre,
    "g/cc": OM2.gramPerCubicCentimetre,
    "g/ml": OM2.gramPerMillilitre,
    "g ml-1": OM2.gramPerMillilitre,
    # Length
    "m": OM2.metre,
    "meter": OM2.metre,
    "metre": OM2.metre,
    "meters": OM2.metre,
    "metres": OM2.metre,
    "cm": OM2.centimetre,
    "centimeter": OM2.centimetre,
    "centimetre": OM2.centimetre,
    "mm": OM2.millimetre,
    "millimeter": OM2.millimetre,
    "millimetre": OM2.millimetre,
    "um": OM2.micrometre,
    "µm": OM2.micrometre,
    "μm": OM2.micrometre,
    "micron": OM2.micrometre,
    "microns": OM2.micrometre,
    "micrometer": OM2.micrometre,
    "micrometre": OM2.micrometre,
    "nm": OM2.nanometre,
    "nanometer": OM2.nanometre,
    "nanometre": OM2.nanometre,
    "nanometers": OM2.nanometre,
    "å": OM2.angstrom,
    "ångstrom": OM2.angstrom,
    "ångström": OM2.angstrom,
    "angstrom": OM2.angstrom,
    "angstroms": OM2.angstrom,
    # Frequency
    "hz": OM2.hertz,
    "hertz": OM2.hertz,
    "khz": OM2.kilohertz,
    "mhz": OM2.megahertz,
    "ghz": OM2.gigahertz,
    "rpm": OM2.revolutionPerMinute,
    "r/min": OM2.revolutionPerMinute,
    "rev/min": OM2.revolutionPerMinute,
    "revolutions per minute": OM2.revolutionPerMinute,
    # Energy / power
    "j": OM2.joule,
    "joule": OM2.joule,
    "joules": OM2.joule,
    "kj": OM2.kilojoule,
    "cal": OM2.calorie,
    "calorie": OM2.calorie,
    "kcal": OM2.kilocalorie,
    "ev": OM2.electronvolt,
    "kev": OM2.electronvolt,
    "j/mol": OM2.joulePerMole,
    "kj/mol": OM2.kilojoulePerMole,
    "kj mol-1": OM2.kilojoulePerMole,
    "kcal/mol": OM2.kilocaloriePerMole,
    "kcal mol-1": OM2.kilocaloriePerMole,
    "w": OM2.watt,
    "watt": OM2.watt,
    "watts": OM2.watt,
    "mw": OM2.milliwatt,
    "kw": OM2.kilowatt,
    # Flow
    "ml/min": OM2.millilitrePerMinute,
    "ml min-1": OM2.millilitrePerMinute,
    "ml/h": OM2.millilitrePerHour,
    "ml h-1": OM2.millilitrePerHour,
    "l/min": OM2.litrePerMinute,
    "l min-1": OM2.litrePerMinute,
    "l/h": OM2.litrePerHour,
    "l h-1": OM2.litrePerHour,
    "cc/min": OM2.cubicCentimetrePerMinute,
    "cm3/min": OM2.cubicCentimetrePerMinute,
    "sccm": OM2.cubicCentimetrePerMinute,
    "ul/min": OM2.microlitrePerMinute,
    "µl/min": OM2.microlitrePerMinute,
    "slm": OM2.litrePerMinute,
    # Molar mass / wavenumber / area
    "g/mol": OM2.gramPerMole,
    "g mol-1": OM2.gramPerMole,
    "da": OM2.dalton,
    "u": OM2.dalton,
    "amu": OM2.dalton,
    "dalton": OM2.dalton,
    "daltons": OM2.dalton,
    "kda": OM2.kilodalton,
    "cm-1": OM2.reciprocalCentimetre,
    "1/cm": OM2.reciprocalCentimetre,
    "wavenumber": OM2.reciprocalCentimetre,
    "m2": OM2.squareMetre,
    "m^2": OM2.squareMetre,
    "m²": OM2.squareMetre,
    "cm2": OM2.squareCentimetre,
    "cm^2": OM2.squareCentimetre,
    "cm²": OM2.squareCentimetre,
    "m2/g": OM2.squareMetrePerGram,
    "m^2/g": OM2.squareMetrePerGram,
    "m²/g": OM2.squareMetrePerGram,
}

_UNIT_QUANTITY_CLASSES: dict[URIRef, frozenset[URIRef]] = {
    OM2.degreeCelsius: frozenset({OM2.Temperature}),
    OM2.degreeFahrenheit: frozenset({OM2.Temperature}),
    OM2.kelvin: frozenset({OM2.Temperature}),
    OM2.degreeCelsiusPerMinute: frozenset({OM2.TemperatureRate}),
    OM2.degreeCelsiusPerHour: frozenset({OM2.TemperatureRate}),
    OM2.degreeCelsiusPerSecond: frozenset({OM2.TemperatureRate}),
    OM2.kelvinPerMinute: frozenset({OM2.TemperatureRate}),
    OM2.kelvinPerHour: frozenset({OM2.TemperatureRate}),
    OM2.kelvinPerSecond: frozenset({OM2.TemperatureRate}),
    OM2.second: frozenset({OM2.Duration}),
    OM2.millisecond: frozenset({OM2.Duration}),
    OM2.microsecond: frozenset({OM2.Duration}),
    OM2.nanosecond: frozenset({OM2.Duration}),
    OM2.picosecond: frozenset({OM2.Duration}),
    OM2.minute: frozenset({OM2.Duration}),
    OM2.hour: frozenset({OM2.Duration}),
    OM2.day: frozenset({OM2.Duration}),
    OM2.week: frozenset({OM2.Duration}),
    OM2.month: frozenset({OM2.Duration}),
    OM2.year: frozenset({OM2.Duration}),
    OM2.pascal: frozenset({OM2.Pressure}),
    OM2.kilopascal: frozenset({OM2.Pressure}),
    OM2.megapascal: frozenset({OM2.Pressure}),
    OM2.gigapascal: frozenset({OM2.Pressure}),
    OM2.hectopascal: frozenset({OM2.Pressure}),
    OM2.bar: frozenset({OM2.Pressure}),
    OM2.kilobar: frozenset({OM2.Pressure}),
    OM2.millibar: frozenset({OM2.Pressure}),
    OM2.standardAtmosphere: frozenset({OM2.Pressure}),
    OM2.torr: frozenset({OM2.Pressure}),
    OM2.millimetreOfMercury: frozenset({OM2.Pressure}),
    OM2.poundForcePerSquareInch: frozenset({OM2.Pressure}),
    OM2.litre: frozenset({OM2.Volume}),
    OM2.millilitre: frozenset({OM2.Volume}),
    OM2.microlitre: frozenset({OM2.Volume}),
    OM2.nanolitre: frozenset({OM2.Volume}),
    OM2.picolitre: frozenset({OM2.Volume}),
    OM2.cubicCentimetre: frozenset({OM2.Volume}),
    OM2.cubicMetre: frozenset({OM2.Volume}),
    OM2.percent: frozenset({OM2.AmountOfSubstanceFraction}),
    OM2.permille: frozenset({OM2.AmountOfSubstanceFraction}),
    OM2.partsPerMillion: frozenset({OM2.AmountOfSubstanceFraction}),
    OM2.partsPerBillion: frozenset({OM2.AmountOfSubstanceFraction}),
    OM2.partsPerTrillion: frozenset({OM2.AmountOfSubstanceFraction}),
    OM2.kilogram: frozenset({OM2.Mass}),
    OM2.gram: frozenset({OM2.Mass}),
    OM2.milligram: frozenset({OM2.Mass}),
    OM2.microgram: frozenset({OM2.Mass}),
    OM2.nanogram: frozenset({OM2.Mass}),
    OM2.picogram: frozenset({OM2.Mass}),
    OM2.mole: frozenset({OM2.AmountOfSubstance}),
    OM2.millimole: frozenset({OM2.AmountOfSubstance}),
    OM2.micromole: frozenset({OM2.AmountOfSubstance}),
    OM2.nanomole: frozenset({OM2.AmountOfSubstance}),
    OM2.picomole: frozenset({OM2.AmountOfSubstance}),
    OM2.equivalent: frozenset({OM2.AmountOfSubstance}),
    OM2.molePerLitre: frozenset({OM2.AmountOfSubstanceConcentration}),
    OM2.millimolePerLitre: frozenset({OM2.AmountOfSubstanceConcentration}),
    OM2.micromolePerLitre: frozenset({OM2.AmountOfSubstanceConcentration}),
    OM2.nanomolePerLitre: frozenset({OM2.AmountOfSubstanceConcentration}),
    OM2.picomolePerLitre: frozenset({OM2.AmountOfSubstanceConcentration}),
    OM2.normal: frozenset({OM2.AmountOfSubstanceConcentration}),
    OM2.gramPerLitre: frozenset({OM2.MassConcentration}),
    OM2.milligramPerLitre: frozenset({OM2.MassConcentration}),
    OM2.milligramPerMillilitre: frozenset({OM2.MassConcentration}),
    OM2.microgramPerMillilitre: frozenset({OM2.MassConcentration}),
    OM2.nanogramPerMillilitre: frozenset({OM2.MassConcentration}),
    OM2.gramPerCubicCentimetre: frozenset({OM2.Density}),
    OM2.gramPerMillilitre: frozenset({OM2.Density}),
    OM2.metre: frozenset({OM2.Length}),
    OM2.centimetre: frozenset({OM2.Length}),
    OM2.millimetre: frozenset({OM2.Length}),
    OM2.micrometre: frozenset({OM2.Length}),
    OM2.nanometre: frozenset({OM2.Length}),
    OM2.angstrom: frozenset({OM2.Length}),
    OM2.hertz: frozenset({OM2.Frequency}),
    OM2.kilohertz: frozenset({OM2.Frequency}),
    OM2.megahertz: frozenset({OM2.Frequency}),
    OM2.gigahertz: frozenset({OM2.Frequency}),
    OM2.revolutionPerMinute: frozenset({OM2.Frequency}),
    OM2.joule: frozenset({OM2.Energy}),
    OM2.kilojoule: frozenset({OM2.Energy}),
    OM2.calorie: frozenset({OM2.Energy}),
    OM2.kilocalorie: frozenset({OM2.Energy}),
    OM2.electronvolt: frozenset({OM2.Energy}),
    OM2.joulePerMole: frozenset({OM2.Energy}),
    OM2.kilojoulePerMole: frozenset({OM2.Energy}),
    OM2.kilocaloriePerMole: frozenset({OM2.Energy}),
    OM2.watt: frozenset({OM2.Power}),
    OM2.milliwatt: frozenset({OM2.Power}),
    OM2.kilowatt: frozenset({OM2.Power}),
    OM2.millilitrePerMinute: frozenset({OM2.VolumetricFlowRate}),
    OM2.millilitrePerHour: frozenset({OM2.VolumetricFlowRate}),
    OM2.litrePerMinute: frozenset({OM2.VolumetricFlowRate}),
    OM2.litrePerHour: frozenset({OM2.VolumetricFlowRate}),
    OM2.cubicCentimetrePerMinute: frozenset({OM2.VolumetricFlowRate}),
    OM2.microlitrePerMinute: frozenset({OM2.VolumetricFlowRate}),
    OM2.gramPerMole: frozenset({OM2.MolarMass}),
    OM2.dalton: frozenset({OM2.MolarMass}),
    OM2.kilodalton: frozenset({OM2.MolarMass}),
    OM2.reciprocalCentimetre: frozenset({OM2.Wavenumber}),
    OM2.squareMetre: frozenset({OM2.Area}),
    OM2.squareCentimetre: frozenset({OM2.Area}),
    OM2.squareMetrePerGram: frozenset({OM2.SpecificSurfaceArea}),
}

_DURATION_SECONDS: dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "ms": 0.001,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "wk": 604800.0,
    "week": 604800.0,
    "weeks": 604800.0,
}

_SUPER_TRANS = str.maketrans(
    {
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
        "⁻": "-",
        "⁺": "+",
    }
)


def normalize_om2_unit_alias(unit: str) -> str:
    """Normalize a source unit label before fixed-map lookup."""
    text = (
        str(unit or "")
        .strip()
        .casefold()
        .replace("º", "°")
        .replace("˚", "°")
        .replace("\u030a", "°")
        .replace("℃", "°c")
        .replace("℉", "°f")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("⁻¹", "-1")
        .replace("·", " ")
        .replace("•", " ")
        .replace("μ", "µ")
        .replace("å", "å")
        .translate(_SUPER_TRANS)
    )
    text = re.sub(r"\^\s*([+-]?\d+)", r"\1", text)
    text = re.sub(r"%\s*(?=yield\b)", "% ", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"%\s*\(\s*w\s*/\s*w\s*\)", "w/w%", text)
    text = re.sub(r"%\s*\(\s*v\s*/\s*v\s*\)", "v/v%", text)
    text = re.sub(r"%\s*\(\s*w\s*/\s*v\s*\)", "w/v%", text)
    text = re.sub(r"%\s*\(\s*wt\.?\s*\)", "wt%", text)
    text = re.sub(r"%\s*\(\s*mol(?:e)?\.?\s*\)", "mol%", text)
    text = re.sub(r"%\s*\(\s*vol(?:ume)?\.?\s*\)", "vol%", text)
    text = re.sub(r"\bper[\s-]?cent(?:age)?\b", "percent", text)
    text = re.sub(r"\bpct\b", "percent", text)
    text = re.sub(r"\bpercent\s+by\s+(?:weight|mass)\b", "percent by weight", text)
    text = re.sub(r"\bpercent\s+by\s+volume\b", "percent by volume", text)
    text = re.sub(r"\bmol(?:e|ecular)?\.?\s*%", "mol%", text)
    text = re.sub(r"\bmol(?:e)?\s+percent\b", "mol%", text)
    text = re.sub(r"\bwt\.?\s*%", "wt%", text)
    text = re.sub(r"\bweight\s+(?:percent|%)", "weight%", text)
    text = re.sub(r"\bmass\s+(?:percent|%)", "mass%", text)
    text = re.sub(r"\bvol(?:ume)?\.?\s*%", "vol%", text)
    text = re.sub(r"\bvolume\s+percent\b", "volume%", text)
    text = re.sub(r"\bat(?:om(?:ic)?)?\.?\s*%", "at%", text)
    text = re.sub(r"\batomic\s+percent\b", "atomic percent", text)
    text = re.sub(r"\bper\s+mille\b", "permille", text)
    text = re.sub(r"\br\.?\s*p\.?\s*m\.?\b", "rpm", text)
    text = re.sub(r"\bequiv(?:alent)?s?\b", "equiv", text)
    text = re.sub(r"/(?:hr|hrs|hour|hours)\b", "/h", text)
    text = re.sub(r"/(?:mins|minute|minutes)\b", "/min", text)
    text = re.sub(r"/(?:sec|secs|second|seconds)\b", "/s", text)
    text = re.sub(r"\b(?:hr|hrs|hour|hours)\s*-\s*1\b", "h-1", text)
    text = re.sub(r"\b(?:mins|minute|minutes)\s*-\s*1\b", "min-1", text)
    text = re.sub(r"\b(?:sec|secs|second|seconds)\s*-\s*1\b", "s-1", text)
    text = re.sub(r"\bdeg\.?\s*c\b", "degc", text)
    text = re.sub(r"\bo\s*c\b", "°c", text)
    text = re.sub(r"\b(c|degc)\s+per\s+(?:h|hour)\b", r"\1/h", text)
    text = re.sub(r"\b(c|degc)\s+per\s+(?:min|minute)\b", r"\1/min", text)
    text = re.sub(r"\b(c|degc)\s+per\s+(?:s|sec|second)\b", r"\1/s", text)
    text = re.sub(r"(°c)\s+per\s+(?:h|hour)\b", r"\1/h", text)
    text = re.sub(r"(°c)\s+per\s+(?:min|minute)\b", r"\1/min", text)
    text = re.sub(r"(°c)\s+per\s+(?:s|sec|second)\b", r"\1/s", text)
    text = re.sub(r"\bper\s+", "/", text)
    text = re.sub(
        r"\b([a-zµ%0-9.°]+)\s+([a-zµ][a-zµ0-9]*)-1\b",
        r"\1/\2",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith(".") and not text.endswith("%."):
        text = text[:-1].rstrip()
    return text


_NUMBER_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_UNIT_BOUNDARY = " \t()[]{}:-,;="
_GROUPING_CLAUSE_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_QUALIFIER_LEAD_RE = re.compile(
    r"^(?:"
    r"(?:yield\s+)?based\s+(?:on|upon)\b|"
    r"yield\b|"
    r"(?:relative|rel\.?)\s+to\b|"
    r"(?:with\s+respect\s+to|wrt)\b|"
    r"(?:vs\.?|versus)\b|"
    r"compared\s+(?:to|with)\b|"
    r"(?:calc(?:ulated)?|isolated|crude|found|theor(?:etical)?|overall)\b|"
    r"(?:by|via|using)\s+(?:nmr|hplc|gc|ea|analysis|lc)\b|"
    r"(?:over|after)\s+\w+\s+steps?\b"
    r")",
    re.IGNORECASE,
)
_QUALITATIVE_PREFIX_RE = re.compile(
    r"^(?:at|under|in|on|to|from|with|using|via|around|about|"
    r"approx(?:imately)?|ca|circa|nearly|roughly|almost)\s+"
)
_DURATION_PAIR_RE = re.compile(
    rf"({_NUMBER_TOKEN})\s*"
    r"(h|hr|hrs|hour|hours|min|mins|minute|minutes|s|sec|secs|second|seconds|"
    r"d|day|days|wk|wks|week|weeks)\b",
    re.IGNORECASE,
)


def _strip_grouping_clauses(text: str) -> str:
    """Drop one level of ``(...)`` / ``[...]`` so nested amounts stay qualifiers."""
    return _GROUPING_CLAUSE_RE.sub(" ", text)


def _alias_matches_class(alias: str, quantity_class: URIRef | None) -> bool:
    if quantity_class is None:
        return True
    unit_iri = OM2_UNIT_MAP.get(alias)
    if unit_iri is None:
        return True
    allowed = _UNIT_QUANTITY_CLASSES.get(unit_iri)
    if allowed is None:
        return True
    return URIRef(quantity_class) in allowed


def _case_marked_match(unit: str) -> tuple[str, str] | None:
    """Return ``(canonical_alias, remainder)`` for capital ``M`` / ``mM`` / ``N``."""
    text = str(unit or "").lstrip()
    for prefix, alias in sorted(
        _CASE_UNIT_ALIASES, key=lambda item: len(item[0]), reverse=True
    ):
        if not text.startswith(prefix):
            continue
        remainder = text[len(prefix) :]
        if not remainder or remainder[0] in _UNIT_BOUNDARY or remainder[0] in "./%":
            return alias, remainder
    return None


def _case_marked_alias(unit: str) -> str | None:
    matched = _case_marked_match(unit)
    return None if matched is None else matched[0]


def _leading_unit_alias(
    unit: str, quantity_class: URIRef | None = None
) -> str | None:
    """Return a known unit token that begins ``unit``, if any."""
    marked = _case_marked_alias(unit)
    if marked is not None and _alias_matches_class(marked, quantity_class):
        return marked
    normalized = normalize_om2_unit_alias(unit)
    for alias in sorted(OM2_UNIT_MAP, key=len, reverse=True):
        if not _alias_matches_class(alias, quantity_class):
            continue
        if normalized == alias:
            return alias
        if not normalized.startswith(alias):
            continue
        remainder = normalized[len(alias) :]
        if not remainder or remainder[0] in _UNIT_BOUNDARY or remainder[0] == "/":
            return alias
    return None


def _is_descriptive_unit_qualifier(remainder: str) -> bool:
    """True when leftover text is source prose, not a second quantity."""
    if not remainder:
        return True
    if remainder[0] in ",;":
        return True
    if remainder[0] not in _UNIT_BOUNDARY:
        return False
    core = re.sub(r"\s+", " ", _strip_grouping_clauses(remainder)).strip(
        _UNIT_BOUNDARY
    )
    if not core:
        return True
    if re.match(r"^(?:yield\s+)?based\s+(?:on|upon)\b", core, re.I):
        return True
    if _QUALIFIER_LEAD_RE.match(core) and not re.match(r"^yield\b", core, re.I):
        return True
    if re.match(r"^yield\b", core, re.I):
        after_yield = core[5:].strip()
        if not after_yield or re.match(r"^based\s+(?:on|upon)\b", after_yield, re.I):
            return True
        return re.search(rf"(?<![a-z]){_NUMBER_TOKEN}", after_yield) is None
    return re.search(rf"(?<![a-z]){_NUMBER_TOKEN}", core) is None


def _recognized_source_unit(
    unit: str, quantity_class: URIRef | None = None
) -> str | None:
    """Return a known unit while tolerating a descriptive source qualifier."""
    marked = _case_marked_match(unit)
    if marked is not None and _alias_matches_class(marked[0], quantity_class):
        alias, remainder = marked
        if not remainder or _is_descriptive_unit_qualifier(remainder):
            return alias
    normalized = normalize_om2_unit_alias(unit)
    for alias in sorted(OM2_UNIT_MAP, key=len, reverse=True):
        if not _alias_matches_class(alias, quantity_class):
            continue
        if normalized == alias:
            return alias
        if not normalized.startswith(alias):
            continue
        remainder = normalized[len(alias) :]
        if _is_descriptive_unit_qualifier(remainder):
            return alias
    return None


def normalize_qualitative_quantity_label(label: str) -> str:
    """Normalize a controlled qualitative quantity term."""
    text = str(label or "").strip().casefold().replace("_", " ")
    text = re.sub(r"[-–—]+", " ", text)
    text = re.sub(r"[./]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _qualitative_candidates(label: str) -> list[str]:
    normalized = normalize_qualitative_quantity_label(label)
    candidates = [normalized]
    current = normalized
    for _ in range(4):
        stripped = _QUALITATIVE_PREFIX_RE.sub("", current, count=1).strip()
        if not stripped or stripped == current:
            break
        candidates.append(stripped)
        current = stripped
    return candidates


def resolve_qualitative_quantity_preset(
    quantity_class: URIRef, label: str
) -> str | None:
    """Resolve supported non-numeric terms without inventing a numeric value."""
    target = URIRef(quantity_class)
    presets: dict[str, str] | None = None
    if target == OM2.Temperature:
        presets = QUALITATIVE_TEMPERATURE_PRESETS
    elif target == OM2.Duration:
        presets = QUALITATIVE_DURATION_PRESETS
    elif target == OM2.Pressure:
        presets = QUALITATIVE_PRESSURE_PRESETS
    if presets is None:
        return None
    for candidate in _qualitative_candidates(label):
        preset = presets.get(candidate)
        if preset is not None:
            return preset
        if target == OM2.Duration and candidate.startswith("until ") and len(
            candidate.split()
        ) >= 2:
            return candidate
    return None


def resolve_om2_unit(unit: str, quantity_class: URIRef | None = None) -> URIRef:
    """Resolve a supported source unit label to an OM-2 unit IRI."""
    text = str(unit or "").strip()
    if text.startswith(("http://", "https://")):
        return URIRef(text)
    if text.lower().startswith(("om-2:", "om2:")):
        return OM2[text.split(":", 1)[1]]
    recognized = _recognized_source_unit(text, quantity_class)
    if recognized is not None:
        return OM2_UNIT_MAP[recognized]
    marked = _case_marked_alias(text)
    if marked is not None and _alias_matches_class(marked, quantity_class):
        return OM2_UNIT_MAP[marked]
    normalized = normalize_om2_unit_alias(unit)
    resolved = OM2_UNIT_MAP.get(normalized)
    if resolved is not None and not _alias_matches_class(normalized, quantity_class):
        resolved = None
    if resolved is None:
        compact = re.sub(r"[^a-z0-9]", "", normalized)
        by_local = {
            str(iri).rsplit("/", 1)[-1].lower(): iri for iri in OM2_UNIT_MAP.values()
        }
        resolved = by_local.get(compact)
    if resolved is None:
        raise ValueError(
            f"Unsupported OM-2 unit label {unit!r}; allowed aliases: "
            + ", ".join(sorted(OM2_UNIT_MAP))
        )
    return resolved


def _fold_unicode_numbers(text: str) -> str:
    text = text.translate(_SUPER_TRANS)
    text = re.sub(r"(\d+)\s*½", lambda match: str(int(match.group(1)) + 0.5), text)
    text = re.sub(r"(\d+)\s*¼", lambda match: str(int(match.group(1)) + 0.25), text)
    text = re.sub(r"(\d+)\s*¾", lambda match: str(int(match.group(1)) + 0.75), text)
    text = text.replace("½", "0.5").replace("¼", "0.25").replace("¾", "0.75")
    text = text.replace("⅓", "0.333").replace("⅔", "0.667")
    return text


def _preprocess_quantity_label(label: str) -> str:
    """Fold common source decorations so the first quantity is recoverable."""
    text = str(label or "").strip()
    text = _fold_unicode_numbers(text)
    text = (
        text.replace("×", "x")
        .replace("∼", "~")
        .replace("≈", "~")
        .replace("≃", "~")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )
    text = re.sub(
        rf"({_NUMBER_TOKEN})\s*[x·•]\s*10\s*\^?\s*\{{?\s*([-+]?\d+)\s*\}}?",
        lambda match: f"{match.group(1)}e{match.group(2)}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"10\s*\^\s*\{?\s*([-+]?\d+)\s*\}?",
        lambda match: f"1e{match.group(1)}",
        text,
    )
    text = re.sub(r"\^\s*\{\s*([+-]?\d+)\s*\}", r"\1", text)
    text = re.sub(r"\^\s*([+-]?\d+)", r"\1", text)
    text = re.sub(
        rf"(?<![\d.])(\d{{1,3}}),(\d+)\s*(?=°|[a-zA-Zµμ%‰])",
        r"\1.\2",
        text,
    )
    text = re.sub(
        rf"(?:from|between)\s+({_NUMBER_TOKEN})\s+(?:to|and|-)\s*{_NUMBER_TOKEN}",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"({_NUMBER_TOKEN})\s+(?:to|and)\s+{_NUMBER_TOKEN}(?=\s*[°a-zA-Zµμ%‰])",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"({_NUMBER_TOKEN})\s*[±]\s*{_NUMBER_TOKEN}(?=\s*[°a-zA-Zµμ%‰])",
        r"\1",
        text,
    )
    text = re.sub(
        rf"({_NUMBER_TOKEN})\s*\(\s*{_NUMBER_TOKEN}\s*\)(?=\s*[°a-zA-Zµμ%‰])",
        r"\1",
        text,
    )
    text = re.sub(
        rf"({_NUMBER_TOKEN})\s*[-~]\s*{_NUMBER_TOKEN}(?=\s*[°a-zA-Zµμ%‰])",
        r"\1",
        text,
    )
    return text


def _parse_compound_duration(text: str) -> tuple[float, str] | None:
    start = _DURATION_PAIR_RE.search(text)
    if start is None:
        return None
    region = text[start.start() :]
    pairs = _DURATION_PAIR_RE.findall(region)
    if len(pairs) < 2:
        return None
    leftover_numbers = re.findall(_NUMBER_TOKEN, _DURATION_PAIR_RE.sub(" ", region))
    if leftover_numbers:
        return None
    first_unit = pairs[0][1].casefold()
    base_seconds = _DURATION_SECONDS[first_unit]
    total = 0.0
    for raw_value, raw_unit in pairs:
        total += float(raw_value) * _DURATION_SECONDS[raw_unit.casefold()]
    canonical = {
        "sec": "s",
        "secs": "s",
        "second": "s",
        "seconds": "s",
        "mins": "min",
        "minute": "min",
        "minutes": "min",
        "hr": "h",
        "hrs": "h",
        "hour": "h",
        "hours": "h",
        "day": "d",
        "days": "d",
        "wk": "week",
        "wks": "week",
        "weeks": "week",
    }.get(first_unit, first_unit)
    return total / base_seconds, canonical


def parse_om2_quantity_label(
    label: str, quantity_class: URIRef | None = None
) -> tuple[float, str]:
    """Parse a compact source quantity such as ``150 °C``.

    Prefer the leftmost recognized unit that matches ``quantity_class`` when
    it is supplied. Parenthetical mass/amount clauses such as
    ``(15 mg, 0.009 mmol)`` stay qualifiers, so a yield like
    ``18% yield (15 mg, 0.009 mmol) based on H2bdc`` keeps ``18 %`` instead
    of the later millimole fragment. A later percent is used only when the
    earlier number has no known unit, e.g. ``0.023 g (52% based on H2DCPP)``.
    Compound durations such as ``2 h 30 min`` collapse to the first unit.
    """
    text = _preprocess_quantity_label(label)
    first_number = re.search(_NUMBER_TOKEN, text)
    first_duration = _DURATION_PAIR_RE.search(text)
    use_compound = False
    if quantity_class is not None and URIRef(quantity_class) == OM2.Duration:
        use_compound = True
    elif (
        quantity_class is None
        and first_number is not None
        and first_duration is not None
        and first_number.start() == first_duration.start()
    ):
        use_compound = True
    if use_compound:
        compound = _parse_compound_duration(text)
        if compound is not None:
            return compound
    first_unrecognized: tuple[float, str] | None = None
    for match in re.finditer(rf"({_NUMBER_TOKEN})", text):
        rest = text[match.end() :].strip()
        if not rest:
            continue
        value = float(match.group(1))
        alias = _recognized_source_unit(rest, quantity_class)
        if alias is not None:
            return value, alias
        leading = _leading_unit_alias(rest, quantity_class)
        if leading is not None:
            return value, leading
        if first_unrecognized is None and quantity_class is None:
            first_unrecognized = (value, rest)
    if first_unrecognized is not None:
        return first_unrecognized
    word_pattern = "|".join(
        sorted(NUMBER_WORD_VALUES, key=len, reverse=True)
    )
    word_match = re.search(
        rf"\b({word_pattern})\s+([^,;0-9]+)",
        text,
        flags=re.IGNORECASE,
    )
    if word_match:
        unit = word_match.group(2).strip()
        alias = _recognized_source_unit(unit, quantity_class)
        if alias is not None or quantity_class is None:
            return (
                NUMBER_WORD_VALUES[word_match.group(1).casefold()],
                alias or unit,
            )
    raise ValueError(
        f"OM-2 quantity label must contain a number and unit: {label!r}"
    )


def find_or_create_om2_quantity(
    graph: Graph,
    *,
    quantity_class: URIRef,
    label: str,
    value: int | float | str,
    unit: str,
    mint_iri: Callable[[str, str], URIRef],
) -> URIRef:
    """Create one occurrence-local quantity.

    The historical function name is retained for generated-package
    compatibility. Quantity values are descriptors of an owning occurrence,
    not globally reusable identities: equal class/value/unit combinations
    therefore still receive distinct IRIs.
    """
    numeric_value = float(value)
    unit_iri = resolve_om2_unit(unit, quantity_class)
    numeric_literal = Literal(numeric_value, datatype=XSD.double)
    iri = mint_iri(str(quantity_class).rsplit("/", 1)[-1], str(label))
    graph.add((iri, RDF.type, quantity_class))
    graph.add((iri, RDFS.label, Literal(str(label).strip())))
    graph.add((iri, OM2.hasNumericalValue, numeric_literal))
    graph.add((iri, OM2.hasUnit, unit_iri))
    return iri


def find_or_create_om2_quantity_from_label(
    graph: Graph,
    *,
    quantity_class: URIRef,
    label: str,
    mint_iri: Callable[[str, str], URIRef],
) -> URIRef:
    """Create one occurrence-local numeric or qualitative quantity.

    The historical function name is retained for generated-package
    compatibility; this function never searches for or reuses an existing
    quantity IRI.
    """
    qualitative_label = resolve_qualitative_quantity_preset(quantity_class, label)
    if qualitative_label is not None:
        iri = mint_iri(
            str(quantity_class).rsplit("/", 1)[-1],
            f"qualitative:{qualitative_label}",
        )
        graph.add((iri, RDF.type, quantity_class))
        graph.add((iri, RDFS.label, Literal(qualitative_label)))
        return iri

    value, unit = parse_om2_quantity_label(label, quantity_class)
    return find_or_create_om2_quantity(
        graph,
        quantity_class=quantity_class,
        label=label,
        value=value,
        unit=unit,
        mint_iri=mint_iri,
    )
