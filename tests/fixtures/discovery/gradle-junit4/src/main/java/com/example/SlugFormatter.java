package com.example;

public class SlugFormatter {
    public String format(String value) {
        return value.trim().toLowerCase().replace(' ', '-');
    }
}
