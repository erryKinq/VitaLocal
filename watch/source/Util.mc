import Toybox.Application;
import Toybox.Lang;
import Toybox.Math;
import Toybox.System;
import Toybox.Time;
import Toybox.Time.Gregorian;

(:background :glance)
module Util {
    // 32 hex digits: accepted as a UUID by the server (the id makes a replayed write a no-op)
    function newId() as String {
        var s = "";
        for (var i = 0; i < 4; i++) {
            s += Math.rand().format("%08x");
        }
        return s;
    }

    function two(n as Number) as String {
        return n.format("%02d");
    }

    // Local day, e.g. "2026-10-04"
    function day() as String {
        var g = Gregorian.info(Time.now(), Time.FORMAT_SHORT);
        return g.year + "-" + two(g.month as Number) + "-" + two(g.day);
    }

    // Local time with offset, e.g. "2026-10-04T19:30:05+02:00"
    function now() as String {
        var g = Gregorian.info(Time.now(), Time.FORMAT_SHORT);
        var off = System.getClockTime().timeZoneOffset / 60;
        var sign = off < 0 ? "-" : "+";
        off = off.abs();
        return day() + "T" + two(g.hour) + ":" + two(g.min) + ":" + two(g.sec) + sign + two(off / 60) + ":" + two(off % 60);
    }

    function clock() as String {
        var g = Gregorian.info(Time.now(), Time.FORMAT_SHORT);
        return two(g.hour) + ":" + two(g.min);
    }

    function hour() as Number {
        return Gregorian.info(Time.now(), Time.FORMAT_SHORT).hour;
    }

    // German decimal: 64.3 -> "64,3"
    function dec(v as Numeric, digits as Number) as String {
        var s = v.toFloat().format("%." + digits + "f");
        var i = s.find(".");
        return i == null ? s : s.substring(0, i) + "," + s.substring(i + 1, null);
    }

    // German thousands: 1550 -> "1.550"
    function thousands(v as Numeric) as String {
        var n = v.toNumber();
        var neg = n < 0;
        n = n.abs();
        var s = "";
        while (n >= 1000) {
            s = "." + pad3((n % 1000).toString()) + s;
            n = n / 1000;
        }
        return (neg ? "-" : "") + n.toString() + s;
    }

    function pad3(s as String) as String {
        while (s.length() < 3) {
            s = "0" + s;
        }
        return s;
    }

    function num(v) as Number {
        return v == null ? 0 : (v as Numeric).toNumber();
    }

    // Persistent value with a default (Storage may hold nothing yet)
    function stored(key as String, fallback) {
        var v = Application.Storage.getValue(key);
        return v == null ? fallback : v;
    }
}
