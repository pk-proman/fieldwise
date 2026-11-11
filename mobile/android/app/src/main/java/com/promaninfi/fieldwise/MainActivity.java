package com.promaninfi.fieldwise;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(FieldwiseHealthPlugin.class);   // permission/battery status, settings shortcuts, duty watchdog
        super.onCreate(savedInstanceState);
    }
}
