package com.example;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class SlugFormatterTest {
    @Test
    public void formatsWordsAsSlug() {
        assertEquals("hello-world", new SlugFormatter().format("Hello World"));
    }
}
