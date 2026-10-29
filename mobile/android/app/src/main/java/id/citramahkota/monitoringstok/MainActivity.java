package id.citramahkota.monitoringstok;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

/** Cangkang Android untuk antarmuka offline dan plugin unduhan pembaruan. */
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(UpdateDownload.class);
        super.onCreate(savedInstanceState);
    }
}
