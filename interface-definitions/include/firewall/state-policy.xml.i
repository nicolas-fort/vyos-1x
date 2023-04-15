<!-- include start from firewall/state-policy.xml.i -->
<node name="state-policy">
  <properties>
    <help>Global firewall state-policy</help>
  </properties>
  <children>
    <node name="established">
      <properties>
        <help>Global firewall policy for packets part of an established connection</help>
      </properties>
      <children>
        #include <include/firewall/action-accept-drop-reject.xml.i>
        #include <include/firewall/log.xml.i>
        #include <include/firewall/rule-log-level.xml.i>
      </children>
    </node>
    <node name="invalid">
      <properties>
        <help>Global firewall policy for packets part of an invalid connection</help>
      </properties>
      <children>
        #include <include/firewall/action-accept-drop-reject.xml.i>
        #include <include/firewall/log.xml.i>
        #include <include/firewall/rule-log-level.xml.i>
      </children>
    </node>
    <node name="related">
      <properties>
        <help>Global firewall policy for packets part of a related connection</help>
      </properties>
      <children>
        #include <include/firewall/action-accept-drop-reject.xml.i>
        #include <include/firewall/log.xml.i>
        #include <include/firewall/rule-log-level.xml.i>
      </children>
    </node>
  </children>
</node>
<!-- include end -->