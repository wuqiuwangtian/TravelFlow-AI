package org.travelflow.p0;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ProbeStorePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
