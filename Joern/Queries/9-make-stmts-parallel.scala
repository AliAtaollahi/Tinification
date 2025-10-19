import io.shiftleft.semanticcpg.language._
import io.shiftleft.codepropertygraph.generated.nodes.{Method, Block, ControlStructure, AstNode}

object CfgByClass {

  // ---------- helpers ----------
  def ownerTypeOf(fullName: String): String =
    fullName.takeWhile(_ != '.')

  def simpleTypeName(tf: String): String =
    Option(tf).getOrElse("").split("[.$]").lastOption.getOrElse(tf)

  // Build "Owner.method(p1, p2, ...)" (skip implicit 'this', prefer names, fallback to types)
  def prettyMethodLabel(m: Method): String = {
    val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
    val params =
      m.parameter.orderGt(0).l.map { p =>
        val nm  = Option(p.name).map(_.trim).filter(n => n.nonEmpty && n != "this")
        val tpe = simpleTypeName(Option(p.typeFullName).getOrElse(""))
        nm.getOrElse(tpe)
      }.mkString(", ")
    owner + "." + m.name + "(" + params + ")"
  }

  // HTML-escape for Graphviz HTML-like labels
  def html(s: String): String =
    s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

  // Graphviz id helpers (stable + unique)
  def sanitize(s: String): String = s.replaceAll("[^A-Za-z0-9_]", "_")
  def stateVarId(owner: String, field: String): String = sanitize(owner) + "__sv_" + sanitize(field)
  def paramId(owner: String, mFull: String, pName: String, idx: Int): String =
    sanitize(owner) + "__" + sanitize(mFull) + "__param_" + idx + "_" + sanitize(pName)
  def ctrlNodeId(owner: String, mFull: String, kind: String, line: Option[Int]): String = {
    val ln = line.map(_.toString).getOrElse("u")
    sanitize(owner) + "__" + sanitize(mFull) + "__ctrl_" + sanitize(kind.toLowerCase) + "_" + ln
  }

  // Prefix numeric ids inside a DOT snippet
  def prefixIds(dot: String, prefix: String): String = {
    val idRx = "\"(\\d+)\"".r
    idRx.replaceAllIn(dot, m => "\"" + prefix + "_" + m.group(1) + "\"")
  }

  // Strip outer digraph wrapper
  def stripDotWrapper(dot: String): String =
    dot.linesIterator
      .filterNot(_.trim.startsWith("digraph"))
      .filterNot(_.trim.startsWith("node "))
      .filterNot(_.trim == "}")
      .mkString("\n")

  // ---------- abstraction with root relabel ----------
  case class Abstracted(
    inner: String,
    lineToKeptNodeId: Map[String,String],  // raw ids (unprefixed)
    rootIds: Set[String],                  // raw ids (unprefixed)
    lineToCode: Map[String,String]
  )

  def abstractDotByLine(dot: String, rootLabelOverride: Option[String] = None): Abstracted = {
    import scala.util.matching.Regex

    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r
    val simpleLabelRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,><]+)>\\s*\\]".r
    val edgeRx: Regex =
      "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

    case class Node(id: String, kind: String, line: String, code: String, methodName: Option[String] = None)

    val nodes: List[Node] =
      nodeRx.findAllMatchIn(dot).map { m =>
        val kind = m.group(2)
        val code = m.group(4)
        val methName =
          if (kind.startsWith("METHOD") || kind.equalsIgnoreCase("init")) Some(code) else None
        Node(m.group(1), kind, m.group(3), code, methName)
      }.toList

    val edges: List[(String, String)] =
      edgeRx.findAllMatchIn(dot).map(m => (m.group(1), m.group(2))).toList

    val labelRootIds: Set[String] =
      simpleLabelRx.findAllMatchIn(dot).map(_.group(1)).toSet

    val srcs = edges.map(_._1).toSet
    val dsts = edges.map(_._2).toSet
    val structuralRootIds: Set[String] = srcs -- dsts
    val rootIds: Set[String] = (labelRootIds ++ structuralRootIds)

    if (nodes.isEmpty && edges.isEmpty)
      return Abstracted("", Map.empty, Set.empty, Map.empty)

    val bestByLine: Map[String, Node] =
      nodes.groupBy(_.line).map { case (line, xs) => line -> xs.maxBy(_.code.length) }

    val keptNodes = bestByLine.values.toList

    val nodeLines: List[String] =
      keptNodes
        .sortBy(n => n.line.toIntOption.getOrElse(Int.MaxValue))
        .map { n =>
          val label =
            n.kind match {
              case k if k.startsWith("METHOD") && n.methodName.isDefined => n.methodName.get
              case k if k.equalsIgnoreCase("init") && n.methodName.isDefined => n.methodName.get
              case _ => n.kind + ", " + n.line + "<BR/>" + n.code
            }
          "\"" + n.id + "\" [label = <" + label + "> ]"
        }

    // Do NOT emit original CFG edges; we add scope-based ones later.
    val edgeLines: List[String] = Nil

    val rootLines: List[String] =
      if (rootIds.nonEmpty) {
        val text = html(rootLabelOverride.getOrElse("METHOD"))
        rootIds.toList.map { id => "\"" + id + "\" [label = <<FONT>" + text + "</FONT>> ]" }
      } else Nil

    val inner = (nodeLines ++ edgeLines ++ rootLines).mkString("\n")
    val keptMap: Map[String,String] = bestByLine.map{ case (ln, n) => ln -> n.id }
    val codeMap: Map[String,String] = bestByLine.map{ case (ln, n) => ln -> n.code }
    Abstracted(inner, keptMap, rootIds, codeMap)
  }

  // ---------- main ----------
  def run(fileRegex: String = ".*\\.java"): String = {
    val methods =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    val methodsByOwner =
      methods.groupBy { m => m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName)) }

    // Inner/user class basenames to filter fields like room/controller/etc.
    val innerClassBaseNames: Set[String] =
      cpg.typeDecl
        .where(_.isExternal(false))
        .name
        .l
        .map { n =>
          val base = n.split("\\$").lastOption.getOrElse(n)
          base.toLowerCase
        }
        .toSet

    // Caches
    val methodToPrefixedRootId = scala.collection.mutable.Map.empty[String, String]
    val methodToLineMap        = scala.collection.mutable.Map.empty[String, Map[String,String]]
    val methodToLineCode       = scala.collection.mutable.Map.empty[String, Map[String,String]]
    val methodToBody           = scala.collection.mutable.Map.empty[String, String]
    val ownerToBodies          = scala.collection.mutable.Map.empty[String, scala.collection.mutable.ListBuffer[String]]

    val methodParamIdxToNodeId = scala.collection.mutable.Map.empty[String, Map[Int, String]]
    val ownerStateVarToNodeId  = scala.collection.mutable.Map.empty[(String,String), String]

    val emittedStateNodeIds    = scala.collection.mutable.Set.empty[String]
    val emittedParamNodeIds    = scala.collection.mutable.Set.empty[String]
    val emittedCtrlNodeIds     = scala.collection.mutable.Set.empty[String]  // synthetic CS nodes

    val blueEdges        = scala.collection.mutable.Set.empty[String]
    val redEdges         = scala.collection.mutable.Set.empty[String]
    val actDepEdges      = scala.collection.mutable.Set.empty[String]
    val purpleArgEdges   = scala.collection.mutable.Set.empty[String]
    val ctrlScopeEdges   = scala.collection.mutable.Set.empty[String]    // only scope-based ctrl deps

    // regexes
    val thisFieldRx        = "\\bthis\\.([A-Za-z_]\\w*)".r
    val assignLhsThisRx    = "\\bthis\\.([A-Za-z_]\\w*)\\s*=".r

    methods.foreach { m =>
      val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
      val pfx   = (owner + "__" + m.fullName).replaceAll("[^A-Za-z0-9_]", "_")
      val raw   = m.dotCfg.l.headOption.getOrElse("")

      val buf = ownerToBodies.getOrElseUpdate(owner, scala.collection.mutable.ListBuffer.empty[String])

      // --- parameter nodes ---
      val params = m.parameter.orderGt(0).l
      val paramIdxToId: Map[Int,String] =
        params.zipWithIndex.map { case (p, i0) =>
          val idx  = i0 + 1
          val name = Option(p.name).getOrElse("param" + idx)
          val pid  = paramId(owner, m.fullName, name, idx)
          if (!emittedParamNodeIds.contains(pid)) {
            val label = sanitize(m.name) + "_param_" + sanitize(name)
            buf += "\"" + pid + "\" [label = <<FONT>" + label + "</FONT>> ]"
            emittedParamNodeIds += pid
          }
          idx -> pid
        }.toMap
      methodParamIdxToNodeId += (m.fullName -> paramIdxToId)

      if (raw.nonEmpty) {
        val rootLabel = prettyMethodLabel(m)
        val abs = abstractDotByLine(raw, Some(rootLabel))
        val withWrapper =
          "digraph \"cfg_abstract\" {\nnode [shape=\"rect\"];\n" + abs.inner + "\n}"
        val prefixed  = prefixIds(withWrapper, pfx)
        val stripped  = stripDotWrapper(prefixed)

        val lineMapPrefixed  = abs.lineToKeptNodeId.map{ case (ln,id) => ln -> (pfx + "_" + id) }
        val lineCode         = abs.lineToCode
        val rootIdsPrefixed  = abs.rootIds.map(id => pfx + "_" + id)

        methodToLineMap += (m.fullName -> lineMapPrefixed)
        methodToLineCode += (m.fullName -> lineCode)
        methodToBody    += (m.fullName -> stripped)
        rootIdsPrefixed.headOption.foreach(r => methodToPrefixedRootId += (m.fullName -> r))

        // --- state var nodes (exclude inner-class-like names) ---
        val allCodes = lineCode.values.mkString("\n")
        val fieldsUsed = thisFieldRx.findAllMatchIn(allCodes).map(_.group(1)).toSet
        val fieldsUsedFiltered = fieldsUsed.filterNot(f => innerClassBaseNames.contains(f.toLowerCase))
        fieldsUsedFiltered.foreach { f =>
          val id = ownerStateVarToNodeId.getOrElseUpdate((owner, f), stateVarId(owner, f))
          if (!emittedStateNodeIds.contains(id)) {
            val label = "sv_" + sanitize(f) + " " + html("this." + f)
            buf += "\"" + id + "\" [label = <<FONT>" + label + "</FONT>> ]"
            emittedStateNodeIds += id
          }
        }

        // --- DATA-DEPS (color semantics swapped as requested) ---
        lineMapPrefixed.foreach { case (ln, nodeId) =>
          val code = lineCode.getOrElse(ln, "")

          // 1) STATE VARS
          val rhsFieldsRaw = thisFieldRx.findAllMatchIn(code).map(_.group(1)).toSet
          val lhsFieldsRaw = assignLhsThisRx.findAllMatchIn(code).map(_.group(1)).toSet
          val rhsCand = rhsFieldsRaw.filterNot(f => innerClassBaseNames.contains(f.toLowerCase))
          val lhsSet  = lhsFieldsRaw.filterNot(f => innerClassBaseNames.contains(f.toLowerCase))

          // Node is influenced by RHS uses  => RED edge from variable to node
          val rhsOnly = rhsCand.diff(lhsSet)
          rhsOnly.foreach { f =>
            val sv = ownerStateVarToNodeId.getOrElseUpdate((owner, f), stateVarId(owner, f))
            redEdges += "\"" + sv + "\" -> \"" + nodeId + "\" [color=\"blue\"]"
          }

          // Node influences variable on LHS  => BLUE edge from node to variable
          lhsSet.foreach { f =>
            val sv = ownerStateVarToNodeId.getOrElseUpdate((owner, f), stateVarId(owner, f))
            blueEdges  += "\"" + nodeId + "\" -> \"" + sv + "\" [color=\"red\"]"
          }

          // 2) PARAMS
          params.zipWithIndex.foreach { case (p, i0) =>
            val idx   = i0 + 1
            val pName = Option(p.name).getOrElse("")
            if (pName.nonEmpty) {
              val qn    = java.util.regex.Pattern.quote(pName)
              val useRx = ( "(?<![\\w$])" + qn + "(?![\\w$])" ).r
              val lhsRx = ( "(?<![\\w$])" + qn + "\\s*=" ).r

              val isLhs = lhsRx.findFirstIn(code).isDefined
              val used  = useRx.findFirstIn(code).isDefined

              if (used && !isLhs) {
                val pid = paramIdxToId(idx)
                redEdges += "\"" + pid + "\" -> \"" + nodeId + "\" [color=\"blue\"]"
              }
              if (isLhs) {
                val pid = paramIdxToId(idx)
                blueEdges += "\"" + nodeId + "\" -> \"" + pid + "\" [color=\"red\"]"
              }
            }
          }
        }

        // --- CONTROL DEPENDENCIES with robust IF/ELSE/ELSE-IF handling ---
        try {
          val mBlockOpt: Option[Block] = m.ast.isBlock.headOption
          val rootIdOpt = rootIdsPrefixed.headOption

          (for {
            methodBlock <- mBlockOpt
            rootId      <- rootIdOpt
          } yield (methodBlock, rootId)).foreach { case (methodBlock, rootId) =>

            // Map an AST node to our kept/abstracted node id via its source line
            def idOf(n: AstNode): Option[String] =
              n.lineNumber.map(_.toString).flatMap(lineMapPrefixed.get)

            // Create or reuse a synthetic control-structure node (IF/WHILE/...)
            def ensureCtrlNode(cs: ControlStructure): String = {
              val kind = Option(cs.controlStructureType).getOrElse("IF")
              val ln   = cs.lineNumber
              val id   = ctrlNodeId(owner, m.fullName, kind, ln)
              if (!emittedCtrlNodeIds.contains(id)) {
                val condText =
                  ln.flatMap(lnv => lineCode.get(lnv.toString)).getOrElse(kind)
                val label = s"${kind.toLowerCase} (${condText})"
                buf += "\"" + id + "\" [label = <<FONT>" + html(label) + "</FONT>> ]"
                emittedCtrlNodeIds += id
              }
              id
            }

            // Attach edges from a CS node to all statements in a given branch
            def attachBranch(parentId: String, cs: ControlStructure, bodyNodes: List[AstNode]): Unit = {
              bodyNodes.foreach {
                case b: Block =>
                  // Recurse through statements of the block
                  b.astChildren.l.foreach {
                    case nestedCs: ControlStructure => addCtrlFromCS(parentId, nestedCs)
                    case n: AstNode                 => idOf(n).foreach(oid => ctrlScopeEdges += s""""$parentId" -> "$oid" [label="ctrl dep"]""")
                    case _ => ()
                  }
                case nestedCs: ControlStructure =>
                  addCtrlFromCS(parentId, nestedCs)
                case n: AstNode =>
                  idOf(n).foreach(oid => ctrlScopeEdges += s""""$parentId" -> "$oid" [label="ctrl dep"]""")
              }
            }

            // Add CS with both branches handled
            def addCtrlFromCS(parentId: String, cs: ControlStructure): Unit = {
              val csNodeId = ensureCtrlNode(cs)
              ctrlScopeEdges += s""""$parentId" -> "$csNodeId" [label="ctrl dep"]"""

              // Prefer Joern branch traversals if present
              val (trueNodes, falseNodes) =
                try {
                  val tNodes = cs.whenTrue.astChildren.collectAll[AstNode].l
                  val fNodes = cs.whenFalse.astChildren.collectAll[AstNode].l
                  (tNodes, fNodes)
                } catch {
                  case _: Throwable =>
                    // Fallback: take all children except the condition (order==1)
                    val nonCond = cs.astChildren.collectAll[AstNode].whereNot(_.order(1)).l
                    // Heuristic: if there are 2+ bodies, assume first is THEN, second is ELSE
                    val (tn, fn) =
                      if (nonCond.size >= 2) (List(nonCond.head), nonCond.tail)
                      else (nonCond, Nil)
                    (tn, fn)
                }

              attachBranch(csNodeId, cs, trueNodes)
              attachBranch(csNodeId, cs, falseNodes)
            }

            // Walk the method body: root → immediate statements/CSs
            def addFrom(parentId: String, block: Block): Unit = {
              block.astChildren.l.foreach {
                case cs: ControlStructure => addCtrlFromCS(parentId, cs)
                case child: AstNode       => idOf(child).foreach { childId =>
                  ctrlScopeEdges += s""""$parentId" -> "$childId" [label="ctrl dep"]"""
                }
                case _ => ()
              }
            }

            addFrom(rootId, methodBlock)
          }
        } catch {
          case _: Throwable => () // best-effort
        }

        // add body to cluster
        buf += stripped
      }
    }

    // --- clusters ---
    val clustersByClass: List[String] =
      methodsByOwner.toList.sortBy(_._1).flatMap { case (owner, ms) =>
        val ownerSan = sanitize(owner)
        val bodies = ownerToBodies.get(owner).map(_.toList).getOrElse(Nil)
        if (bodies.isEmpty) None
        else Some(
          "subgraph \"cluster_" + ownerSan + "\" {\n" +
          "  label=\"" + html(owner) + "\";\n" +
          "  " + bodies.mkString("\n") + "\n" +
          "}"
        )
      }

    // --- act-dep (callsite -> callee root) ---
    val internalFullNames: Set[String] = methodToPrefixedRootId.keySet.toSet
    methods.foreach { callerM =>
      val callerMap  = methodToLineMap.getOrElse(callerM.fullName, Map.empty)
      callerM.call.l.foreach { c =>
        val callee = Option(c.methodFullName).getOrElse("")
        val lineOpt = c.lineNumber.map(_.toString)
        if (lineOpt.isDefined && internalFullNames.contains(callee)) {
          val ln = lineOpt.get
          val srcIdOpt = callerMap.get(ln)
          val dstIdOpt = methodToPrefixedRootId.get(callee)
          if (srcIdOpt.isDefined && dstIdOpt.isDefined && srcIdOpt.get != dstIdOpt.get) {
            actDepEdges += "\"" + srcIdOpt.get + "\" -> \"" + dstIdOpt.get + "\" [label=\"act dep\"]"
          }
        }
      }
    }

    // --- argument binding (callsite -> callee param nodes) ---
    methods.foreach { callerM =>
      val callerMap = methodToLineMap.getOrElse(callerM.fullName, Map.empty)
      callerM.call.l.foreach { c =>
        val callee = Option(c.methodFullName).getOrElse("")
        val lineOpt = c.lineNumber.map(_.toString)
        val idxMapOpt = methodParamIdxToNodeId.get(callee)
        if (lineOpt.isDefined && idxMapOpt.isDefined) {
          val callNodeOpt = callerMap.get(lineOpt.get)
          if (callNodeOpt.isDefined) {
            idxMapOpt.get.toList.sortBy(_._1).foreach { case (_, pid) =>
              purpleArgEdges += "\"" + callNodeOpt.get + "\" -> \"" + pid + "\" [color=\"purple\"]"
            }
          }
        }
      }
    }

    // --- final DOT ---
    val out =
      "digraph \"program_cfg_abstract_by_class\" {\n" +
      "compound=true;\n" +
      "node [shape=\"rect\"];\n\n" +
      clustersByClass.mkString("\n\n") + "\n\n" +
      (ctrlScopeEdges ++ actDepEdges ++ purpleArgEdges ++ blueEdges ++ redEdges).mkString("\n") + "\n" +
      "}"

    println(out)
    out
  }
}

val dot = CfgByClass.run()
import java.nio.file.{Files, Paths}
import java.nio.charset.StandardCharsets

val out = Paths.get("program_cfg_abstract_by_class.dot")
if (out.getParent != null) Files.createDirectories(out.getParent)
Files.write(out, dot.getBytes(StandardCharsets.UTF_8))
println("Wrote DOT to: " + out.toAbsolutePath)
