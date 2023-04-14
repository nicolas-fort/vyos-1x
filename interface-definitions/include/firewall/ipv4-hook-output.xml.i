<!-- include start from firewall/ipv4-hook-output.xml.i -->
<node name="output">
  <properties>
    <help>IPv4 output firewall</help>
  </properties>
  <children>
    <node name="filter">
      <properties>
        <help>IPv4 firewall output filter</help>
      </properties>
      <children>
        #include <include/firewall/default-action.xml.i>
        #include <include/firewall/enable-default-log.xml.i>
        #include <include/generic-description.xml.i>
        <leafNode name="default-jump-target">
          <properties>
            <help>Set jump target. Action jump must be defined in default-action to use this setting</help>
            <completionHelp>
              <path>firewall ip name</path>
            </completionHelp>
          </properties>
        </leafNode>
        <tagNode name="rule">
          <properties>
            <help>IP Firewall output filter rule number</help>
            <valueHelp>
              <format>u32:1-999999</format>
              <description>Number for this firewall rule</description>
            </valueHelp>
            <constraint>
              <validator name="numeric" argument="--range 1-999999"/>
            </constraint>
            <constraintErrorMessage>Firewall rule number must be between 1 and 999999</constraintErrorMessage>
          </properties>
          <children>
            #include <include/firewall/common-rule-ipv4.xml.i>
            #include <include/firewall/outbound-interface.xml.i>
            <leafNode name="jump-target">
              <properties>
                <help>Set jump target. Action jump must be defined to use this setting</help>
                <completionHelp>
                  <path>firewall ip name</path>
                </completionHelp>
              </properties>
            </leafNode>
          </children>
        </tagNode>
      </children>
    </node>
  </children>
</node>
<!-- include end -->