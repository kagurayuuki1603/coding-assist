package com.example;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class CounterTest {
    @Test
    public void incrementsValue() {
        assertEquals(2, new Counter().increment(1));
    }
}
